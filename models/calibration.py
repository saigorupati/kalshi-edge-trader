"""
Per-city model calibration using historical DynamoDB records.

Computes bias correction and sigma scale from recent forecast errors:
    bias  = mean(actual_high - nbm_mu)         [systematic shift]
    scale = std(actual_high - nbm_mu) / mean(nbm_sigma)  [spread accuracy]

Updates city configs in memory. Called once at startup and daily at 09:00.
"""

import datetime
import logging
from typing import Optional, Tuple

import numpy as np
import scipy.stats as scipy_stats

from config import CITIES, CityConfig

logger = logging.getLogger(__name__)

MIN_RECORDS_FOR_CALIBRATION = 7  # Need at least this many actuals
BIAS_CONFIDENCE_T_THRESHOLD = 1.5  # Min t-statistic to apply bias (~85% confidence)
REGIME_SHIFT_THRESHOLD_F = 1.5  # °F divergence between 7d and 30d bias to trigger blend


def compute_bias_correction(
    records: list,
) -> Tuple[float, float]:
    """
    Computes bias and sigma scale from a list of calibration records.

    Args:
        records: list of dicts with keys: nbm_mu, nbm_sigma, actual_high

    Returns:
        (bias_correction, sigma_scale)
        Falls back to (0.0, 1.0) if insufficient data.
    """
    if len(records) < MIN_RECORDS_FOR_CALIBRATION:
        logger.info(
            "Insufficient calibration records (%d < %d) — using defaults",
            len(records), MIN_RECORDS_FOR_CALIBRATION,
        )
        return 0.0, 1.0

    actuals = np.array([r["actual_high"] for r in records])
    mus = np.array([r["nbm_mu"] for r in records])
    sigmas = np.array([r["nbm_sigma"] for r in records])

    errors = actuals - mus  # Positive = NBM underpredicts
    bias = float(np.mean(errors))

    # Confidence gate: only apply bias if it's statistically significant.
    # With few samples or noisy errors, the bias estimate could be noise.
    std_err = float(np.std(errors, ddof=1)) / np.sqrt(len(errors))
    if std_err > 0:
        t_stat = abs(bias) / std_err
        if t_stat < BIAS_CONFIDENCE_T_THRESHOLD:
            logger.info(
                "Bias %.2f°F not significant (t=%.2f < %.1f, n=%d) — zeroing",
                bias, t_stat, BIAS_CONFIDENCE_T_THRESHOLD, len(records),
            )
            bias = 0.0

    # Sigma scale: how much larger should our sigma be vs NBM's reported sigma
    # A scale > 1 means NBM is overconfident
    if np.mean(sigmas) > 0:
        actual_spread = float(np.std(errors))
        scale = actual_spread / float(np.mean(sigmas))
        scale = max(0.5, min(scale, 2.5))  # clamp to reasonable range
    else:
        scale = 1.0

    logger.info(
        "Calibration: bias=%.2f°F scale=%.3f (n=%d records, RMSE=%.2f°F)",
        bias, scale, len(records), float(np.sqrt(np.mean(errors ** 2))),
    )
    return bias, scale


def update_city_calibration(db_client) -> None:
    """
    Recomputes bias and sigma scale for each city using DynamoDB history.
    Uses a 30-day window by default. If the 7-day bias diverges from the
    30-day bias by more than REGIME_SHIFT_THRESHOLD_F, blends toward recent
    data (70% 7-day / 30% 30-day) to handle model updates or weather regime
    shifts faster.

    Updates CITIES config in-memory.

    Args:
        db_client: DynamoClient instance
    """
    cutoff_7d = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    for city_code, city_cfg in CITIES.items():
        try:
            records_30d = db_client.get_calibration_history(city_code, lookback_days=30)
            records_7d = [r for r in records_30d if r["forecast_date"] >= cutoff_7d]

            bias_30d, scale_30d = compute_bias_correction(records_30d)
            bias_7d, scale_7d = compute_bias_correction(records_7d)

            # Regime shift detection: if recent 7-day window diverges sharply,
            # weight it more heavily so corrections kick in faster.
            if (
                len(records_7d) >= MIN_RECORDS_FOR_CALIBRATION
                and abs(bias_7d - bias_30d) > REGIME_SHIFT_THRESHOLD_F
            ):
                bias = 0.7 * bias_7d + 0.3 * bias_30d
                scale = 0.7 * scale_7d + 0.3 * scale_30d
                logger.info(
                    "Regime shift detected for %s (7d_bias=%.2f°F vs 30d_bias=%.2f°F)"
                    " — blending: bias=%.2f°F scale=%.3f",
                    city_code, bias_7d, bias_30d, bias, scale,
                )
            else:
                bias, scale = bias_30d, scale_30d

            city_cfg.bias_correction = bias
            city_cfg.sigma_scale = scale

            logger.info(
                "Updated calibration %s: bias=%.2f°F sigma_scale=%.3f (n30=%d n7=%d)",
                city_code, bias, scale, len(records_30d), len(records_7d),
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
