"""
Temperature distribution modeling.

Converts NBM probabilistic forecasts into Normal distributions,
applies per-city calibration, and computes bin probabilities
for Kalshi temperature range contracts.
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from scipy import stats
from scipy.optimize import brentq

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
    skewness_a: float = 0.0   # Skew-normal shape param (0 = symmetric Normal)
    t_df: float = 30.0        # t-dist df for tail inflation (higher = more Gaussian)


def _fit_skewnorm_from_percentiles(p10: float, p50: float, p90: float) -> float:
    """
    Estimates the skew-normal shape parameter `a` from NBM p10/p50/p90.

    The asymmetry ratio  R = (p90 - p50) / (p50 - p10)  captures how the
    upper tail compares to the lower tail:
      R > 1  →  right-skewed  (NBM sees more upside risk)  →  a > 0
      R < 1  →  left-skewed   (NBM sees more downside risk) →  a < 0
      R ≈ 1  →  symmetric, returns 0.0

    Uses brentq root-finding on the standardised skew-normal quantile ratio.
    Returns `a` clamped to [-10, 10].
    """
    upper = p90 - p50
    lower = p50 - p10

    if lower <= 0 or upper <= 0:
        return 0.0  # Degenerate percentiles — fall back to symmetric

    target_ratio = upper / lower
    if abs(target_ratio - 1.0) < 0.02:
        return 0.0  # Close enough to symmetric — skip fitting

    def residual(a: float) -> float:
        d = stats.skewnorm(a)
        q10 = d.ppf(0.10)
        q50 = d.ppf(0.50)
        q90 = d.ppf(0.90)
        denom = q50 - q10
        if abs(denom) < 1e-9:
            return float("inf")
        return (q90 - q50) / denom - target_ratio

    try:
        a = brentq(residual, -10.0, 10.0, xtol=1e-3, maxiter=50)
        return float(np.clip(a, -10.0, 10.0))
    except Exception:
        return 0.0  # Fallback to symmetric if root-finding fails


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

    # Fit skew-normal shape from NBM p10/p50/p90 asymmetry
    skewness_a = _fit_skewnorm_from_percentiles(
        p10=forecast.p10,
        p50=forecast.p50,
        p90=forecast.p90,
    )

    logger.debug(
        "TempDist %s: raw_mu=%.1f → adj_mu=%.1f | raw_sigma=%.1f → adj_sigma=%.1f "
        "(bias=%.1f scale=%.2f skew_a=%.2f t_df=%.1f)",
        city.name, raw_mu, adj_mu, raw_sigma, adj_sigma,
        city.bias_correction, city.sigma_scale, skewness_a, city.t_df,
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
        skewness_a=skewness_a,
        t_df=city.t_df,
    )


def bin_probability(
    mu: float,
    sigma: float,
    temp_low: Optional[float],
    temp_high: Optional[float],
    is_open_low: bool,
    is_open_high: bool,
    skewness_a: float = 0.0,
    t_df: float = 30.0,
) -> float:
    """
    Computes P(temp falls in a Kalshi bin) using a skew-normal distribution
    with t-distribution tail inflation.

    Two improvements over a plain Normal:
      1. Skew-normal (skewness_a): captures NBM p10/p50/p90 asymmetry so the
         model is not forced to treat upside and downside risk as equal.
      2. t-inflation (t_df): sigma is inflated by sqrt(t_df/(t_df-2)) to
         account for the heavier-than-Gaussian tails seen in real forecast
         errors.  At t_df=30 this is a ~1.7% widening; at t_df=5 it's ~29%.

    is_open_low:  bin is "X° or lower" → P(T <= temp_high)
    is_open_high: bin is "X° or higher" → P(T >= temp_low)
    else:         P(temp_low <= T <= temp_high)
    """
    # Inflate sigma for heavier tails (t-distribution inflation factor)
    if t_df > 2.0:
        sigma_eff = sigma * math.sqrt(t_df / (t_df - 2.0))
    else:
        sigma_eff = sigma

    dist = stats.skewnorm(a=skewness_a, loc=mu, scale=sigma_eff)

    if is_open_low and temp_high is not None:
        return float(dist.cdf(temp_high))

    if is_open_high and temp_low is not None:
        return float(1.0 - dist.cdf(temp_low))

    if temp_low is not None and temp_high is not None:
        return float(dist.cdf(temp_high) - dist.cdf(temp_low))

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
            skewness_a=dist.skewness_a,
            t_df=dist.t_df,
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
