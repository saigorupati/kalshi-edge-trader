"""
Trade execution: ties together Kelly sizing, risk management,
and Kalshi order placement.

Supports paper / demo / live modes via TRADING_MODE env var.
"""

import logging
import datetime
from typing import List, Optional

from config import TRADING_MODE, MAX_POSITION_PCT_PER_CITY
from data.kalshi import KalshiClient
from trading.edge import TradeOpportunity
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
    ) -> Optional[dict]:
        """
        Full execution pipeline for one trade opportunity:
        1. Compute Kelly fraction → contract count
        2. Check risk controls
        3. Place order (paper/demo/live)
        4. Log to DynamoDB
        5. Register with risk manager

        Returns order result dict or None if rejected.
        """
        # 1. Sizing
        k_frac = kelly_fraction(opp.model_prob, opp.ask_price)
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
                "%s: Kelly sizing too small to trade %s (kelly_frac=%.4f, risk=$%.2f)",
                city, opp.market.ticker, k_frac, dollar_risk,
            )
            return None

        # 2. Risk check
        allowed, reason = self.risk.can_trade(city, dollar_risk, self.current_balance)
        if not allowed:
            logger.info("%s: Risk check blocked trade on %s — %s", city, opp.market.ticker, reason)
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
            }
            trade_id = self.db.put_trade(trade_record)
            logger.info(
                "Trade logged: id=%s | %s | %s | x%d @ %d¢ | edge=%.1f%% | risk=$%.2f",
                trade_id[:8], city, opp.market.ticker, count, ask_cents,
                opp.net_edge * 100, dollar_risk,
            )
        except Exception as e:
            logger.error("Failed to log trade to DynamoDB: %s", e)
            trade_id = "log-failed"

        # 5. Register with risk manager
        self.risk.register_trade(city, dollar_risk)

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
        Executes the best (highest net_edge) opportunity for a city.
        At most 1 trade per city per call to avoid over-concentration.

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
            "%s: Best opportunity %s | model_prob=%.1f%% ask=%.2f edge=%.1f%%",
            city,
            best.market.ticker,
            best.model_prob * 100,
            best.ask_price,
            best.net_edge * 100,
        )

        result = self.execute_opportunity(best, city)
        return [result] if result else []
