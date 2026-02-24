"""
Per-city model calibration using historical DynamoDB records.

Computes bias correction and sigma scale from recent forecast errors:
    bias  = mean(actual_high - nbm_mu)         [systematic shift]
    scale = std(actual_high - nbm_mu) / mean(nbm_sigma)  [spread accuracy]

Updates city configs in memory. Called once at startup and daily at 09:00.
"""

import datetime
import logging
import math
from typing import Optional, Tuple

import numpy as np
import scipy.stats as scipy_stats

from config import CITIES, CityConfig

logger = logging.getLogger(__name__)

MIN_RECORDS_FOR_CALIBRATION = 7  # Need at least this many actuals
BIAS_CONFIDENCE_T_THRESHOLD = 1.5  # Min t-statistic to apply bias (~85% confidence)
REGIME_SHIFT_THRESHOLD_F = 1.5  # °F divergence between 7d and 90d bias to trigger blend


def _seasonal_weight(record_date_str: str, reference_date: datetime.date) -> float:
    """
    Cosine-squared seasonal proximity weight based on day-of-year distance.

    Weight = cos²(day_of_year_diff * π / 180)
      ≈ 1.0 at 0 days apart (same calendar date)
      ≈ 0.5 at ±45 days apart
      ≈ 0.0 at ±90 days apart

    Handles year-boundary wrap so Jan 5 and Dec 31 are 5 days apart, not 360.
    """
    try:
        record_date = datetime.date.fromisoformat(record_date_str)
    except (ValueError, TypeError):
        return 1.0  # Unparseable date — don't penalise it

    ref_yday = reference_date.timetuple().tm_yday
    rec_yday = record_date.timetuple().tm_yday

    raw_diff = abs(ref_yday - rec_yday)
    day_diff = min(raw_diff, 365 - raw_diff)  # Circular distance

    return math.cos(day_diff * math.pi / 180.0) ** 2


def compute_bias_correction(
    records: list,
    reference_date: Optional[datetime.date] = None,
) -> Tuple[float, float, float]:
    """
    Computes bias, sigma scale, and t-distribution df from calibration records,
    applying seasonal proximity weighting so records from the same time of year
    receive higher weight.

    Args:
        records: list of dicts with keys: nbm_mu, nbm_sigma, actual_high,
                 forecast_date (ISO string)
        reference_date: date used for seasonal weighting (defaults to today)

    Returns:
        (bias_correction, sigma_scale, t_df)
        Falls back to (0.0, 1.0, 30.0) if insufficient data.
    """
    if len(records) < MIN_RECORDS_FOR_CALIBRATION:
        logger.info(
            "Insufficient calibration records (%d < %d) — using defaults",
            len(records), MIN_RECORDS_FOR_CALIBRATION,
        )
        return 0.0, 1.0, 30.0

    if reference_date is None:
        reference_date = datetime.date.today()

    # Seasonal proximity weights: cos²(day_of_year_diff * π/180)
    weights = np.array([
        _seasonal_weight(r.get("forecast_date", ""), reference_date)
        for r in records
    ])
    weight_sum = float(weights.sum())
    if weight_sum == 0:
        weights = np.ones(len(records))
        weight_sum = float(len(records))

    actuals = np.array([r["actual_high"] for r in records])
    mus     = np.array([r["nbm_mu"]      for r in records])
    sigmas  = np.array([r["nbm_sigma"]   for r in records])

    errors = actuals - mus  # Positive = NBM underpredicts

    # Weighted bias
    bias = float(np.average(errors, weights=weights))

    # Confidence gate using weighted effective sample size.
    # N_eff = (Σw)² / Σ(w²)  — analogous to unweighted n.
    w_sq_sum = float(np.sum(weights ** 2))
    n_eff = (weight_sum ** 2) / w_sq_sum if w_sq_sum > 0 else float(len(records))
    weighted_var = float(np.average((errors - bias) ** 2, weights=weights))
    std_err = math.sqrt(weighted_var / n_eff) if n_eff > 1 else 0.0

    if std_err > 0:
        t_stat = abs(bias) / std_err
        if t_stat < BIAS_CONFIDENCE_T_THRESHOLD:
            logger.info(
                "Bias %.2f°F not significant (t=%.2f < %.1f, n_eff=%.1f) — zeroing",
                bias, t_stat, BIAS_CONFIDENCE_T_THRESHOLD, n_eff,
            )
            bias = 0.0

    # Weighted sigma scale: how much larger our sigma should be vs NBM's
    mean_sigma = float(np.average(sigmas, weights=weights))
    if mean_sigma > 0:
        actual_spread = math.sqrt(float(np.average((errors - bias) ** 2, weights=weights)))
        scale = actual_spread / mean_sigma
        scale = max(0.5, min(scale, 2.5))
    else:
        scale = 1.0

    # Fit t-distribution to (unweighted) residuals to quantify tail heaviness.
    # Lower df = heavier tails than Gaussian; clamp to [3, 50].
    t_df = 30.0
    try:
        df_fit, _, _ = scipy_stats.t.fit(errors, floc=0)
        t_df = float(np.clip(df_fit, 3.0, 50.0))
    except Exception:
        pass  # Keep default 30.0 if fitting fails

    logger.info(
        "Calibration: bias=%.2f°F scale=%.3f t_df=%.1f "
        "(n=%d n_eff=%.1f RMSE=%.2f°F)",
        bias, scale, t_df,
        len(records), n_eff, float(np.sqrt(np.mean(errors ** 2))),
    )
    return bias, scale, t_df


def update_city_calibration(db_client) -> None:
    """
    Recomputes bias, sigma scale, and t-distribution df for each city using
    DynamoDB history.  Uses a 90-day seasonally-weighted window.  If the
    7-day bias diverges from the 90-day bias by more than
    REGIME_SHIFT_THRESHOLD_F, blends toward recent data (70/30) so
    corrections kick in faster after model updates or weather regime shifts.

    Updates CITIES config in-memory.

    Args:
        db_client: DynamoClient instance
    """
    today = datetime.date.today()
    cutoff_7d = (today - datetime.timedelta(days=7)).isoformat()

    for city_code, city_cfg in CITIES.items():
        try:
            records_90d = db_client.get_calibration_history(city_code, lookback_days=90)
            records_7d  = [r for r in records_90d if r["forecast_date"] >= cutoff_7d]

            bias_90d, scale_90d, t_df = compute_bias_correction(records_90d, reference_date=today)
            bias_7d,  scale_7d,  _    = compute_bias_correction(records_7d,  reference_date=today)

            # Regime shift detection: blend toward recent 7-day window when
            # it diverges sharply from the longer-term 90-day estimate.
            if (
                len(records_7d) >= MIN_RECORDS_FOR_CALIBRATION
                and abs(bias_7d - bias_90d) > REGIME_SHIFT_THRESHOLD_F
            ):
                bias  = 0.7 * bias_7d  + 0.3 * bias_90d
                scale = 0.7 * scale_7d + 0.3 * scale_90d
                logger.info(
                    "Regime shift detected for %s (7d_bias=%.2f°F vs 90d_bias=%.2f°F)"
                    " — blending: bias=%.2f°F scale=%.3f",
                    city_code, bias_7d, bias_90d, bias, scale,
                )
            else:
                bias, scale = bias_90d, scale_90d

            city_cfg.bias_correction = bias
            city_cfg.sigma_scale     = scale
            city_cfg.t_df            = t_df

            logger.info(
                "Updated calibration %s: bias=%.2f°F sigma_scale=%.3f t_df=%.1f "
                "(n90=%d n7=%d)",
                city_code, bias, scale, t_df, len(records_90d), len(records_7d),
            )
        except Exception as e:
            logger.error("Calibration update failed for %s: %s", city_code, e)


def store_forecast_calibration(db_client, city_code: str, forecast, nws_high: Optional[float] = None) -> None:
    """
    Stores NBM forecast params to DynamoDB for future calibration.
    Called each cycle after fetching NBM data.

    Args:
        db_client:  DynamoClient instance
        city_code:  e.g. "LA"
        forecast:   NBMForecast dataclass
        nws_high:   Optional NWS sanity check value
    """
    try:
        db_client.put_calibration(
            city=city_code,
            forecast_date=forecast.valid_date,
            cycle=forecast.run_cycle,
            nbm_mu=forecast.mu,
            nbm_sigma=forecast.sigma,
            nws_sanity_check=nws_high,
        )
    except Exception as e:
        logger.error("Failed to store calibration for %s: %s", city_code, e)


def fill_actual_highs(db_client, nws_fetcher) -> None:
    """
    Called daily at 09:00 to backfill yesterday's actual high temperatures
    into calibration records (for model improvement over time).

    Args:
        db_client:   DynamoClient instance
        nws_fetcher: callable(city_cfg, date) → Optional[float]
    """
    import datetime
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    for city_code, city_cfg in CITIES.items():
        try:
            actual = nws_fetcher(city_cfg)
            if actual is not None:
                for cycle in ["19", "13", "07", "01"]:
                    db_client.update_calibration_actual(
                        city=city_code,
                        forecast_date=yesterday,
                        cycle=cycle,
                        actual_high=actual,
                    )
                logger.info("Filled actual high for %s %s: %.1f°F", city_code, yesterday, actual)
        except Exception as e:
            logger.error("Failed to fill actual high for %s: %s", city_code, e)
