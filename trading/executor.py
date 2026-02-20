"""
Trade execution: ties together Kelly sizing, risk management,
and Kalshi order placement.

Supports paper / demo / live modes via TRADING_MODE env var.
"""

import logging
import datetime
import uuid
from typing import List, Optional

from config import TRADING_MODE, MAX_POSITION_PCT_PER_CITY
from data.kalshi import KalshiClient
from trading.edge import TradeOpportunity, BracketOpportunity
from trading.sizing import kelly_fraction, compute_contract_count, max_risk_for_city
from trading.risk import RiskManager

logger = logging.getLogger(__name__)


class TradeExecutor:
    def __init__(
        self,
        kalshi_client: KalshiClient,
        risk_manager: RiskManager,
        db_client,
        current_balance: float,
    ):
        self.client = kalshi_client
        self.risk = risk_manager
        self.db = db_client
        self.current_balance = current_balance

    def update_balance(self, balance: float) -> None:
        self.current_balance = balance

    # ------------------------------------------------------------------
    # Single trade execution
    # ------------------------------------------------------------------

    def execute_opportunity(
        self,
        opp: TradeOpportunity,
        city: str,
        strategy: str = "single",
        bracket_id: Optional[str] = None,
        budget_override: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Full execution pipeline for one trade opportunity:
        1. Compute Kelly fraction → contract count
        2. Check risk controls
        3. Place order (paper/demo/live)
        4. Log to DynamoDB
        5. Register with risk manager

        Args:
            strategy:        "single" or "bracket" — stored in DynamoDB for P&L comparison.
            bracket_id:      Shared UUID string for both legs of a bracket (None for single).
            budget_override: If set, use this as the city budget cap instead of city_remaining.
                             Used by bracket execution to split the budget evenly across legs.

        Returns order result dict or None if rejected.
        """
        # 1. Sizing
        k_frac = kelly_fraction(opp.model_prob, opp.ask_price)
        if budget_override is not None:
            city_remaining = budget_override
        else:
            city_remaining = max_risk_for_city(
                city, self.current_balance, self.risk.city_exposure(city)
            )
        count, dollar_risk = compute_contract_count(
            k_frac,
            self.current_balance,
            opp.ask_price,
            city_remaining,
        )

        if count < 1:
            logger.info(
                "%s: Kelly sizing too small to trade %s (kelly_frac=%.4f, risk=$%.2f, strategy=%s)",
                city, opp.market.ticker, k_frac, dollar_risk, strategy,
            )
            return None

        # 2. Risk check
        allowed, reason = self.risk.can_trade(
            city, dollar_risk, self.current_balance, market_ticker=opp.market.ticker
        )
        if not allowed:
            logger.info(
                "%s: Risk check blocked trade on %s — %s (strategy=%s)",
                city, opp.market.ticker, reason, strategy,
            )
            return None

        # 3. Place order
        ask_cents = round(opp.ask_price * 100)
        order_result = self.client.place_order(
            ticker=opp.market.ticker,
            side="yes",
            action="buy",
            count=count,
            yes_price_cents=ask_cents,
        )

        if order_result is None:
            logger.error("%s: Order placement failed for %s", city, opp.market.ticker)
            return None

        order_id = order_result.get("order", {}).get("order_id", "")

        # 4. Log to DynamoDB
        try:
            trade_record = {
                "city": city,
                "ticker": opp.market.ticker,
                "side": "yes",
                "action": "buy",
                "count": count,
                "price_cents": ask_cents,
                "model_prob": opp.model_prob,
                "edge": opp.net_edge,
                "kelly_fraction": k_frac,
                "dollar_risk": dollar_risk,
                "mode": TRADING_MODE,
                "order_id": order_id,
                "strategy": strategy,
                "bracket_id": bracket_id,
            }
            trade_id = self.db.put_trade(trade_record)
            logger.info(
                "Trade logged: id=%s | %s | %s | x%d @ %d¢ | edge=%.1f%% | risk=$%.2f | strategy=%s",
                trade_id[:8], city, opp.market.ticker, count, ask_cents,
                opp.net_edge * 100, dollar_risk, strategy,
            )
        except Exception as e:
            logger.error("Failed to log trade to DynamoDB: %s", e)
            trade_id = "log-failed"

        # 5. Register with risk manager
        self.risk.register_trade(city, dollar_risk, market_ticker=opp.market.ticker)

        return {
            "trade_id": trade_id,
            "order_id": order_id,
            "ticker": opp.market.ticker,
            "count": count,
            "price_cents": ask_cents,
            "dollar_risk": dollar_risk,
            "model_prob": opp.model_prob,
            "net_edge": opp.net_edge,
            "kelly_fraction": k_frac,
            "mode": TRADING_MODE,
            "strategy": strategy,
            "bracket_id": bracket_id,
        }

    # ------------------------------------------------------------------
    # City-level execution (picks best opportunity)
    # ------------------------------------------------------------------

    def execute_city_opportunities(
        self,
        city: str,
        opportunities: List[TradeOpportunity],
    ) -> List[dict]:
        """
        Executes the best (highest net_edge) single-bin opportunity for a city.
        At most 1 trade per city per call to avoid over-concentration.
        All trades are tagged strategy="single".

        Returns list of executed trade dicts.
        """
        if not opportunities:
            return []

        if self.risk.kill_switch_active:
            logger.warning("%s: Kill switch active — skipping", city)
            return []

        # Pick the single best opportunity
        best = opportunities[0]

        logger.info(
            "%s: Best single opportunity %s | model_prob=%.1f%% ask=%.2f edge=%.1f%%",
            city,
            best.market.ticker,
            best.model_prob * 100,
            best.ask_price,
            best.net_edge * 100,
        )

        result = self.execute_opportunity(best, city, strategy="single")
        return [result] if result else []

    # ------------------------------------------------------------------
    # Bracket trade execution
    # ------------------------------------------------------------------

    def execute_bracket_opportunity(
        self,
        bracket: BracketOpportunity,
        city: str,
    ) -> Optional[List[dict]]:
        """
        Executes both legs of a bracket trade.

        The city's remaining risk budget is split evenly: each leg gets half.
        Both legs share a bracket_id (UUID) so they can be grouped in DynamoDB.

        Returns a list of 1–2 executed trade dicts, or None if both legs are
        rejected by sizing/risk.  A partial fill (one leg) returns a single-item
        list — the trade is still logged; caller decides whether to surface this.
        """
        if self.risk.kill_switch_active:
            logger.warning("%s: Kill switch active — skipping bracket", city)
            return None

        shared_bracket_id = str(uuid.uuid4())
        city_remaining = max_risk_for_city(
            city, self.current_balance, self.risk.city_exposure(city)
        )
        per_leg_budget = city_remaining / 2.0

        logger.info(
            "%s: Executing bracket %s+%s | combined_prob=%.1f%% total_ask=%.2f EV=%.1f%% "
            "per_leg_budget=$%.2f bracket_id=%s",
            city,
            bracket.legs[0].market.ticker,
            bracket.legs[1].market.ticker,
            bracket.combined_model_prob * 100,
            bracket.total_ask,
            bracket.expected_value * 100,
            per_leg_budget,
            shared_bracket_id[:8],
        )

        results = []
        for leg in bracket.legs:
            res = self.execute_opportunity(
                leg,
                city,
                strategy="bracket",
                bracket_id=shared_bracket_id,
                budget_override=per_leg_budget,
            )
            if res is not None:
                results.append(res)

        if not results:
            logger.info("%s: Bracket rejected — both legs failed sizing/risk", city)
            return None

        return results

    def execute_city_with_bracket(
        self,
        city: str,
        single_opps: List[TradeOpportunity],
        bracket_opps: List[BracketOpportunity],
    ) -> List[dict]:
        """
        Runs both strategies independently for a city and returns all executed trades.

        - Single-bin: best opportunity tagged strategy="single"
        - Bracket: best bracket opportunity (if any) tagged strategy="bracket"

        Both strategies share the same city risk budget independently — the risk
        manager's per-city exposure cap ensures neither over-commits.
        """
        if self.risk.kill_switch_active:
            logger.warning("%s: Kill switch active — skipping all strategies", city)
            return []

        all_results: List[dict] = []

        # --- Single-bin leg ---
        single_results = self.execute_city_opportunities(city, single_opps)
        all_results.extend(single_results)

        # --- Bracket leg ---
        if bracket_opps:
            best_bracket = bracket_opps[0]  # already sorted by EV descending
            bracket_results = self.execute_bracket_opportunity(best_bracket, city)
            if bracket_results:
                all_results.extend(bracket_results)

        return all_results
