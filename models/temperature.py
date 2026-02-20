"""
Temperature distribution modeling.

Converts NBM probabilistic forecasts into Normal distributions,
applies per-city calibration, and computes bin probabilities
for Kalshi temperature range contracts.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from scipy import stats

from config import CityConfig
from data.weather import NBMForecast
from data.kalshi import KalshiMarket

logger = logging.getLogger(__name__)


@dataclass
class TempDistribution:
    city: str
    valid_date: str           # "YYYY-MM-DD"
    mu: float                 # Calibration-adjusted mean (°F)
    sigma: float              # Calibration-adjusted std dev (°F)
    raw_mu: float             # NBM raw median before calibration
    raw_sigma: float          # NBM raw sigma before calibration
    bias_applied: float       # Bias correction applied
    sigma_scale_applied: float


def fit_normal_from_nbm(forecast: NBMForecast, city: CityConfig) -> TempDistribution:
    """
    Converts NBM forecast into a calibration-adjusted Normal distribution.

    Calibration:
        mu'    = forecast.p50 + city.bias_correction
        sigma' = raw_sigma    * city.sigma_scale

    The city calibration params are updated daily from DynamoDB history.
    """
    raw_mu = forecast.mu       # = p50
    raw_sigma = forecast.sigma

    adj_mu = raw_mu + city.bias_correction
    adj_sigma = max(raw_sigma * city.sigma_scale, 1.0)  # floor at 1°F

    logger.debug(
        "TempDist %s: raw_mu=%.1f → adj_mu=%.1f | raw_sigma=%.1f → adj_sigma=%.1f "
        "(bias=%.1f, scale=%.2f)",
        city.name, raw_mu, adj_mu, raw_sigma, adj_sigma,
        city.bias_correction, city.sigma_scale,
    )

    return TempDistribution(
        city=city.name,
        valid_date=forecast.valid_date,
        mu=adj_mu,
        sigma=adj_sigma,
        raw_mu=raw_mu,
        raw_sigma=raw_sigma,
        bias_applied=city.bias_correction,
        sigma_scale_applied=city.sigma_scale,
    )


def bin_probability(
    mu: float,
    sigma: float,
    temp_low: Optional[float],
    temp_high: Optional[float],
    is_open_low: bool,
    is_open_high: bool,
) -> float:
    """
    Computes P(temp falls in a Kalshi bin) under Normal(mu, sigma).

    is_open_low:  bin is "X° or lower" → P(T <= temp_high)
    is_open_high: bin is "X° or higher" → P(T >= temp_low)
    else:         P(temp_low <= T <= temp_high)
    """
    norm = stats.norm(loc=mu, scale=sigma)

    if is_open_low and temp_high is not None:
        return float(norm.cdf(temp_high))

    if is_open_high and temp_low is not None:
        return float(1.0 - norm.cdf(temp_low))

    if temp_low is not None and temp_high is not None:
        return float(norm.cdf(temp_high) - norm.cdf(temp_low))

    return 0.0


def compute_market_probabilities(
    dist: TempDistribution,
    markets: List[KalshiMarket],
) -> List[Tuple[KalshiMarket, float]]:
    """
    Computes model probability for each market.
    Only processes markets within mu ± 4*sigma to avoid noise.
    """
    results = []
    bounds_low = dist.mu - 4 * dist.sigma
    bounds_high = dist.mu + 4 * dist.sigma

    for mkt in markets:
        # Skip markets clearly outside our distribution range
        if mkt.temp_low is not None and mkt.temp_low > bounds_high:
            continue
        if mkt.temp_high is not None and mkt.temp_high < bounds_low:
            continue

        prob = bin_probability(
            dist.mu, dist.sigma,
            mkt.temp_low, mkt.temp_high,
            mkt.is_open_low, mkt.is_open_high,
        )
        results.append((mkt, prob))

    # Sort by probability descending
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def find_bracket_markets(
    dist: TempDistribution,
    markets: List[KalshiMarket],
    bracket_half_width: float = 2.0,
) -> List[Tuple[KalshiMarket, float]]:
    """
    Identifies the ~4°F bracket of bins centered around mu.
    Returns markets within [mu - bracket_half_width, mu + bracket_half_width]
    as the core high-probability range to focus on.

    These are the contracts most likely to resolve YES and where
    the edge is most concentrated.
    """
    all_probs = compute_market_probabilities(dist, markets)

    bracket = []
    for mkt, prob in all_probs:
        in_range = False
        center = dist.mu

        if mkt.temp_low is not None and mkt.temp_high is not None:
            bin_center = (mkt.temp_low + mkt.temp_high) / 2
            if abs(bin_center - center) <= bracket_half_width + 1.0:
                in_range = True
        elif mkt.is_open_low and mkt.temp_high is not None:
            if mkt.temp_high >= center - bracket_half_width - 2.0:
                in_range = True
        elif mkt.is_open_high and mkt.temp_low is not None:
            if mkt.temp_low <= center + bracket_half_width + 2.0:
                in_range = True

        if in_range:
            bracket.append((mkt, prob))

    return bracket
