"""
Edge detection and expected value calculation.

Computes whether a Kalshi contract offers positive expected value
after accounting for fees and spread, using our temperature model.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from config import MIN_EDGE_THRESHOLD, KALSHI_FEE_RATE
from data.kalshi import KalshiClient, KalshiMarket, KalshiOrderbook
from models.temperature import TempDistribution, bin_probability

logger = logging.getLogger(__name__)

MAX_SPREAD_TO_TRADE = 0.12   # Skip illiquid markets with spread > 12 cents
MIN_VOLUME_TO_TRADE = 5      # Skip markets with very low volume
MIN_ASK_TO_TRADE = 0.05      # Skip markets priced below 5¢ — fee makes them unprofitable
MAX_ASK_TO_TRADE = 0.95      # Skip near-certain markets — no meaningful edge possible
BRACKET_MIN_EDGE  = 0.10     # Minimum sum of leg net_edges to enter a bracket trade


@dataclass
class TradeOpportunity:
    market: KalshiMarket
    orderbook: KalshiOrderbook
    model_prob: float          # P(YES resolves) from our model
    ask_price: float           # Best YES ask (0.0–1.0)
    bid_price: float           # Best YES bid (0.0–1.0)
    spread: float              # ask - bid
    raw_edge: float            # model_prob - ask_price
    fee_cost: float            # Estimated fee fraction per contract
    net_edge: float            # raw_edge - fee_cost
    has_edge: bool             # net_edge > MIN_EDGE_THRESHOLD
    ev_per_dollar: float       # Expected return per $ risked = net_edge / ask_price
    city: str = ""


def compute_edge(
    model_prob: float,
    ask_price: float,
) -> Tuple[float, float, float]:
    """
    Computes raw edge, fee cost, and net edge for a YES buy.

    Args:
        model_prob: Our estimated P(YES resolves)
        ask_price:  Best YES ask (0.0–1.0)

    Returns:
        (raw_edge, fee_cost, net_edge)

    Fee model: Kalshi charges ~1% of notional (payout) per contract,
    i.e. $0.01 per $1 contract.  As a fraction of the premium paid,
    fee_cost = KALSHI_FEE_RATE / ask_price.  At 5¢ ask this is 20% —
    far exceeding any realistic edge — which is why we also gate on
    MIN_ASK_TO_TRADE in evaluate_market.
    """
    raw_edge = model_prob - ask_price
    # Fee expressed as a fraction of premium (not flat cents)
    fee_cost = KALSHI_FEE_RATE / ask_price if ask_price > 0 else 1.0
    net_edge = raw_edge - fee_cost
    return raw_edge, fee_cost, net_edge


def evaluate_market(
    market: KalshiMarket,
    dist: TempDistribution,
    client: KalshiClient,
    city: str,
) -> Optional[TradeOpportunity]:
    """
    Fetches orderbook for one market and evaluates the edge.
    Returns a TradeOpportunity or None if the market is unattractive.
    """
    # Get real-time orderbook
    ob = client.get_orderbook(market.ticker)
    if ob is None:
        return None

    ask = ob.best_ask()
    bid = ob.best_bid()

    if ask is None or ask <= 0.01 or ask >= 0.99:
        return None  # Near-trivial market

    # Gate on tradeable ask range before computing anything else
    if ask < MIN_ASK_TO_TRADE:
        logger.debug(
            "Skipping %s: ask %.2f below min %.2f (fee would exceed any edge)",
            market.ticker, ask, MIN_ASK_TO_TRADE,
        )
        return None
    if ask > MAX_ASK_TO_TRADE:
        logger.debug("Skipping %s: ask %.2f above max %.2f", market.ticker, ask, MAX_ASK_TO_TRADE)
        return None

    spread = ob.spread() or 1.0
    if spread > MAX_SPREAD_TO_TRADE:
        logger.debug("Skipping %s: spread too wide (%.2f)", market.ticker, spread)
        return None

    if market.volume < MIN_VOLUME_TO_TRADE:
        logger.debug("Skipping %s: low volume (%d)", market.ticker, market.volume)
        return None

    # Guard: skip market if temp range could not be parsed from subtitle
    if market.temp_low is None and market.temp_high is None \
            and not market.is_open_low and not market.is_open_high:
        logger.debug(
            "Skipping %s: could not parse temp range from subtitle %r",
            market.ticker, market.yes_sub_title,
        )
        return None

    # Compute model probability for this bin
    model_prob = bin_probability(
        dist.mu, dist.sigma,
        market.temp_low, market.temp_high,
        market.is_open_low, market.is_open_high,
    )

    # A model_prob of 0.0 means the bin is outside our distribution —
    # do not treat this as a tradeable edge against a low ask price.
    if model_prob <= 0.0:
        logger.debug("Skipping %s: model_prob=0.0 (bin outside distribution)", market.ticker)
        return None

    raw_edge, fee_cost, net_edge = compute_edge(model_prob, ask)
    has_edge = net_edge >= MIN_EDGE_THRESHOLD
    ev_per_dollar = net_edge / ask if ask > 0 else 0.0

    return TradeOpportunity(
        market=market,
        orderbook=ob,
        model_prob=model_prob,
        ask_price=ask,
        bid_price=bid or 0.0,
        spread=spread,
        raw_edge=raw_edge,
        fee_cost=fee_cost,
        net_edge=net_edge,
        has_edge=has_edge,
        ev_per_dollar=ev_per_dollar,
        city=city,
    )


def find_opportunities(
    dist: TempDistribution,
    markets: List[KalshiMarket],
    client: KalshiClient,
    city: str,
) -> List[TradeOpportunity]:
    """
    Evaluates all markets near mu and returns TradeOpportunity objects
    sorted by net_edge descending.

    Only evaluates markets within mu ± 4*sigma.
    """
    bounds_low = dist.mu - 4 * dist.sigma
    bounds_high = dist.mu + 4 * dist.sigma
    opportunities = []

    for mkt in markets:
        # Quick range filter before API call
        if mkt.temp_low is not None and mkt.temp_low > bounds_high:
            continue
        if mkt.temp_high is not None and mkt.temp_high < bounds_low:
            continue

        opp = evaluate_market(mkt, dist, client, city)
        if opp is not None:
            opportunities.append(opp)

    opportunities.sort(key=lambda o: o.net_edge, reverse=True)

    if opportunities:
        logger.info(
            "%s: evaluated %d markets, best edge=%.1f%% on %s",
            city,
            len(opportunities),
            opportunities[0].net_edge * 100,
            opportunities[0].market.ticker,
        )
    return opportunities


def filter_viable_opportunities(
    opportunities: List[TradeOpportunity],
    min_edge: float = MIN_EDGE_THRESHOLD,
) -> List[TradeOpportunity]:
    """Returns only opportunities where net_edge > min_edge."""
    viable = [o for o in opportunities if o.net_edge >= min_edge]
    if not viable:
        logger.debug("No viable opportunities (threshold=%.1f%%)", min_edge * 100)
    return viable


# ---------------------------------------------------------------------------
# Bracket strategy (buy 2 adjacent bounded bins centred around mu)
# ---------------------------------------------------------------------------

@dataclass
class BracketOpportunity:
    """
    Two adjacent bounded bins that straddle mu.

    One leg resolves YES if the actual high falls in its bin — exactly one
    leg (or neither) can win.  We buy both, so:

        profit_if_hit  = 1.0 - total_ask      (net payout per contract)
        EV per contract = combined_prob * profit_if_hit
                        - (1 - combined_prob) * total_ask
                        = combined_prob - total_ask   (same form as single-bin)
    """
    legs: List[TradeOpportunity]           # always 2 elements
    combined_model_prob: float             # sum of leg model_probs
    total_ask: float                       # sum of leg ask_prices (cost per bracket)
    profit_if_hit: float                   # 1.0 - total_ask
    total_net_edge: float                  # sum of leg net_edges
    expected_value: float                  # combined_prob - total_ask
    has_edge: bool                         # total_net_edge > BRACKET_MIN_EDGE
    city: str


def find_bracket_opportunities(
    dist,                              # TempDistribution
    opportunities: List[TradeOpportunity],
    city: str,
) -> List["BracketOpportunity"]:
    """
    From already-evaluated single-bin opportunities, find adjacent bounded-bin
    pairs that straddle mu and pass all three gates:

        1. Each leg individually: net_edge >= MIN_EDGE_THRESHOLD
        2. total_net_edge >= BRACKET_MIN_EDGE (10%)
        3. EV > 0  i.e.  combined_model_prob > total_ask

    Returns at most 1 BracketOpportunity per call (the best-scoring pair).
    Open-ended bins (is_open_low / is_open_high) are excluded because their
    bin centres are undefined and they cannot be adjacent to a bounded bin in
    a sensible way.
    """
    # Keep only fully-bounded bins that individually pass the min edge gate
    bounded = [
        o for o in opportunities
        if not o.market.is_open_low
        and not o.market.is_open_high
        and o.market.temp_low is not None
        and o.market.temp_high is not None
        and o.net_edge >= MIN_EDGE_THRESHOLD
    ]

    if len(bounded) < 2:
        return []

    # Sort ascending by temp_low so adjacent pairs can be found by index
    bounded.sort(key=lambda o: o.market.temp_low)

    results: List[BracketOpportunity] = []

    for i in range(len(bounded) - 1):
        lo = bounded[i]
        hi = bounded[i + 1]

        # Must be truly adjacent: hi.temp_low == lo.temp_high + 1
        # (Kalshi bins are whole-degree, so adjacent means consecutive integers)
        if hi.market.temp_low != lo.market.temp_high + 1:
            continue

        # Bin centres relative to mu
        lo_centre = (lo.market.temp_low + lo.market.temp_high) / 2.0
        hi_centre = (hi.market.temp_low + hi.market.temp_high) / 2.0

        # The pair must straddle mu (one centre below, one above)
        if not (lo_centre <= dist.mu <= hi_centre):
            continue

        combined_prob = lo.model_prob + hi.model_prob
        total_ask     = lo.ask_price + hi.ask_price
        profit_if_hit = 1.0 - total_ask
        total_net_edge = lo.net_edge + hi.net_edge
        ev            = combined_prob - total_ask  # simplified form

        # Gate 2: combined net edge
        if total_net_edge < BRACKET_MIN_EDGE:
            logger.debug(
                "%s: Bracket %s+%s skipped: total_net_edge=%.1f%% < %.1f%%",
                city, lo.market.ticker, hi.market.ticker,
                total_net_edge * 100, BRACKET_MIN_EDGE * 100,
            )
            continue

        # Gate 3: positive EV
        if ev <= 0:
            logger.debug(
                "%s: Bracket %s+%s skipped: EV=%.3f <= 0 (combined_prob=%.1f%%, total_ask=%.2f)",
                city, lo.market.ticker, hi.market.ticker,
                ev, combined_prob * 100, total_ask,
            )
            continue

        results.append(BracketOpportunity(
            legs=[lo, hi],
            combined_model_prob=combined_prob,
            total_ask=total_ask,
            profit_if_hit=profit_if_hit,
            total_net_edge=total_net_edge,
            expected_value=ev,
            has_edge=True,
            city=city,
        ))

    if results:
        # Sort by EV descending; return the best bracket
        results.sort(key=lambda b: b.expected_value, reverse=True)
        logger.info(
            "%s: %d bracket candidate(s), best EV=%.1f%% "
            "(%s + %s, combined_prob=%.1f%%, total_ask=%.2f)",
            city, len(results),
            results[0].expected_value * 100,
            results[0].legs[0].market.ticker,
            results[0].legs[1].market.ticker,
            results[0].combined_model_prob * 100,
            results[0].total_ask,
        )

    return results
