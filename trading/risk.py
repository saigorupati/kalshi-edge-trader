"""
Risk management: daily loss limits, position caps, kill switch.

The RiskManager keeps in-memory state per trading day.
On startup, state is rebuilt from DynamoDB open trades.
"""

import logging
import datetime
from typing import Dict, Optional, Tuple

from config import (
    DAILY_STOP_LOSS_PCT,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_PCT_PER_CITY,
)

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, starting_balance: float):
        self._day_start_balance = starting_balance
        self._current_balance = starting_balance
        self._kill_switch_active = False
        self._today = datetime.date.today()
        self._open_position_count = 0
        self._city_exposure: Dict[str, float] = {}  # city → dollars at risk
        self._open_tickers: set = set()              # market tickers with open positions

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    def reset_daily(self, current_balance: float) -> None:
        """Call at the start of each new trading day."""
        self._today = datetime.date.today()
        self._day_start_balance = current_balance
        self._current_balance = current_balance
        self._kill_switch_active = False
        self._open_position_count = 0
        self._city_exposure = {}
        self._open_tickers = set()
        logger.info(
            "Daily risk reset: balance=%.2f | date=%s",
            current_balance, self._today,
        )

    def update_balance(self, balance: float) -> None:
        """Update the tracked balance (called after each cycle sync)."""
        self._current_balance = balance

    # ------------------------------------------------------------------
    # Kill switch
    # ------------------------------------------------------------------

    def check_kill_switch(self, current_balance: float) -> bool:
        """
        Returns True if kill switch should be active.
        Triggered when current_balance < day_start * (1 - DAILY_STOP_LOSS_PCT).
        Once triggered, stays active for the rest of the day.
        """
        if self._kill_switch_active:
            return True

        loss_threshold = self._day_start_balance * (1.0 - DAILY_STOP_LOSS_PCT)
        if current_balance < loss_threshold:
            self._kill_switch_active = True
            loss_pct = (self._day_start_balance - current_balance) / self._day_start_balance
            logger.critical(
                "KILL SWITCH ACTIVATED: balance=%.2f < threshold=%.2f (loss=%.1f%%)",
                current_balance, loss_threshold, loss_pct * 100,
            )
        return self._kill_switch_active

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------

    def can_trade(
        self,
        city: str,
        dollar_risk: float,
        current_balance: float,
        market_ticker: str = "",
    ) -> Tuple[bool, str]:
        """
        Validates all pre-trade risk controls.
        Returns (allowed, reason_string).
        """
        if self._kill_switch_active:
            return False, "Kill switch active — no trading today"

        if self._open_position_count >= MAX_OPEN_POSITIONS:
            return False, f"Max open positions reached ({MAX_OPEN_POSITIONS})"

        if market_ticker and market_ticker in self._open_tickers:
            return False, f"Already have an open position in {market_ticker}"

        city_budget = MAX_POSITION_PCT_PER_CITY * current_balance
        city_used = self._city_exposure.get(city, 0.0)
        if city_used + dollar_risk > city_budget:
            return False, (
                f"City {city} exposure would exceed budget "
                f"(used={city_used:.2f} + risk={dollar_risk:.2f} > budget={city_budget:.2f})"
            )

        absolute_max = MAX_POSITION_PCT_PER_CITY * current_balance
        if dollar_risk > absolute_max:
            return False, (
                f"Single trade risk {dollar_risk:.2f} exceeds max {absolute_max:.2f}"
            )

        if dollar_risk <= 0:
            return False, "Computed dollar risk is zero — no trade to make"

        return True, "OK"

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def register_trade(self, city: str, dollar_risk: float, market_ticker: str = "") -> None:
        """Record a new open position."""
        self._open_position_count += 1
        self._city_exposure[city] = self._city_exposure.get(city, 0.0) + dollar_risk
        if market_ticker:
            self._open_tickers.add(market_ticker)
        logger.debug(
            "Registered trade: city=%s ticker=%s risk=%.2f | open_positions=%d",
            city, market_ticker, dollar_risk, self._open_position_count,
        )

    def close_position(self, city: str, dollar_risk: float, market_ticker: str = "") -> None:
        """Reduce city exposure when a position resolves."""
        self._open_position_count = max(0, self._open_position_count - 1)
        current = self._city_exposure.get(city, 0.0)
        self._city_exposure[city] = max(0.0, current - dollar_risk)
        if market_ticker:
            self._open_tickers.discard(market_ticker)

    def rebuild_from_open_trades(self, open_trades: list) -> None:
        """
        Rebuild in-memory exposure state from DynamoDB open trade records.
        Called on startup to restore state after a container restart.
        """
        today = datetime.date.today().isoformat()
        for trade in open_trades:
            if trade.get("trade_date") == today:
                city = trade["city"]
                risk = trade.get("dollar_risk", 0.0) or 0.0
                ticker = trade.get("ticker", "")
                self._open_position_count += 1
                self._city_exposure[city] = self._city_exposure.get(city, 0.0) + risk
                if ticker:
                    self._open_tickers.add(ticker)
        if open_trades:
            logger.info(
                "Rebuilt risk state from %d open trades: positions=%d exposure=%s",
                len(open_trades),
                self._open_position_count,
                {k: f"${v:.2f}" for k, v in self._city_exposure.items()},
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active

    @property
    def open_position_count(self) -> int:
        return self._open_position_count

    def city_exposure(self, city: str) -> float:
        return self._city_exposure.get(city, 0.0)

    def status_summary(self) -> dict:
        return {
            "kill_switch": self._kill_switch_active,
            "open_positions": self._open_position_count,
            "max_positions": MAX_OPEN_POSITIONS,
            "day_start_balance": self._day_start_balance,
            "city_exposure": dict(self._city_exposure),
            "open_tickers": list(self._open_tickers),
        }
