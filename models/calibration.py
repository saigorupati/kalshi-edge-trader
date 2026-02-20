"""
Per-city model calibration using historical DynamoDB records.

Computes bias correction and sigma scale from recent forecast errors:
    bias  = mean(actual_high - nbm_mu)         [systematic shift]
    scale = std(actual_high - nbm_mu) / mean(nbm_sigma)  [spread accuracy]

Updates city configs in memory. Called once at startup and daily at 09:00.
"""

import logging
from typing import Optional, Tuple

import numpy as np

from config import CITIES, CityConfig

logger = logging.getLogger(__name__)

MIN_RECORDS_FOR_CALIBRATION = 7  # Need at least this many actuals


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
    Updates CITIES config in-memory.

    Args:
        db_client: DynamoClient instance
    """
    for city_code, city_cfg in CITIES.items():
        try:
            records = db_client.get_calibration_history(city_code, lookback_days=30)
            bias, scale = compute_bias_correction(records)

            city_cfg.bias_correction = bias
            city_cfg.sigma_scale = scale

            logger.info(
                "Updated calibration %s: bias=%.2f°F sigma_scale=%.3f (n=%d)",
                city_code, bias, scale, len(records),
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
