"""
Kelly Criterion position sizing for Kalshi binary contracts.

Full Kelly for a binary contract (buy YES at price q, payout $1 if YES):
    f* = (p - q) / (1 - q)
    where p = model probability of YES resolving
          q = ask price (cost per $1 payout)

    Rewritten: f* = edge / (net payout per $ risked)

We use quarter-Kelly (KELLY_FRACTION = 0.25) for safety.
"""

import logging
import math
from typing import Tuple

from config import KELLY_FRACTION, MAX_POSITION_PCT_PER_CITY

logger = logging.getLogger(__name__)


def kelly_fraction(model_prob: float, ask_price: float) -> float:
    """
    Computes fractional Kelly bet size as a fraction of bankroll.

    Args:
        model_prob: Our P(YES resolves)
        ask_price:  Cost per contract (0.0–1.0, e.g. 0.45 = 45 cents)

    Returns:
        Fraction of total bankroll to risk (e.g. 0.02 = 2%)
        Clamped to [0, MAX_POSITION_PCT_PER_CITY].
    """
    net_payout_per_dollar = 1.0 - ask_price  # How much we gain per $ risked if YES

    if net_payout_per_dollar <= 0 or ask_price <= 0:
        return 0.0

    full_kelly = (model_prob - ask_price) / net_payout_per_dollar
    full_kelly = max(full_kelly, 0.0)  # No negative bets

    fractional = KELLY_FRACTION * full_kelly

    # Cap at max position per city
    capped = min(fractional, MAX_POSITION_PCT_PER_CITY)

    logger.debug(
        "Kelly: model_prob=%.3f ask=%.2f → full_kelly=%.3f → frac_kelly=%.3f → capped=%.3f",
        model_prob, ask_price, full_kelly, fractional, capped,
    )
    return capped


def compute_contract_count(
    kelly_frac: float,
    current_balance: float,
    ask_price: float,          # 0.0–1.0
    max_dollar_risk: float,
) -> Tuple[int, float]:
    """
    Converts a Kelly fraction into an integer number of contracts.

    Args:
        kelly_frac:       Kelly fraction of bankroll to risk
        current_balance:  Current total balance in dollars
        ask_price:        Cost per contract in dollars (0.0–1.0)
        max_dollar_risk:  Absolute dollar cap for this trade

    Returns:
        (contract_count, actual_dollar_risk)
        contract_count = 0 means no trade.
    """
    if ask_price <= 0 or kelly_frac <= 0:
        return 0, 0.0

    dollar_risk = kelly_frac * current_balance
    dollar_risk = min(dollar_risk, max_dollar_risk)
    dollar_risk = max(dollar_risk, 0.0)

    count = math.floor(dollar_risk / ask_price)
    actual_risk = count * ask_price

    if count < 1:
        logger.debug(
            "Contract count too low: kelly_frac=%.4f balance=%.2f ask=%.2f → 0 contracts",
            kelly_frac, current_balance, ask_price,
        )

    return count, actual_risk


def max_risk_for_city(
    city: str,
    current_balance: float,
    existing_city_exposure: float,
) -> float:
    """
    Returns remaining risk budget for a city in dollars.

    Budget = MAX_POSITION_PCT_PER_CITY * current_balance
    Remaining = max(0, Budget - existing_city_exposure)
    """
    budget = MAX_POSITION_PCT_PER_CITY * current_balance
    remaining = max(0.0, budget - existing_city_exposure)
    logger.debug(
        "Risk budget %s: total=%.2f used=%.2f remaining=%.2f",
        city, budget, existing_city_exposure, remaining,
    )
    return remaining
