"""
Portfolio balance and PnL tracking.

In paper mode: maintains an internal paper balance.
In demo/live mode: syncs from Kalshi API balance.
"""

import logging
import datetime
from typing import Optional

from config import STARTING_BALANCE, TRADING_MODE

logger = logging.getLogger(__name__)


class PortfolioTracker:
    def __init__(self, db_client, kalshi_client=None):
        self.db = db_client
        self.kalshi = kalshi_client
        self._balance = STARTING_BALANCE
        self._paper_balance = STARTING_BALANCE
        self._start_date = datetime.date.today()

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    def sync_balance(self) -> float:
        """
        Fetches current balance.
        In live/demo mode: pulls from Kalshi API.
        In paper mode: returns internal paper balance.
        """
        if TRADING_MODE == "paper":
            return self._paper_balance

        if self.kalshi is not None:
            try:
                balance = self.kalshi.get_balance()
                self._balance = balance
                return balance
            except Exception as e:
                logger.error("Failed to sync balance from Kalshi: %s", e)

        return self._balance

    def adjust_paper_balance(self, pnl: float) -> None:
        """Adjust paper trading balance by PnL amount."""
        self._paper_balance += pnl
        logger.debug("Paper balance: %.2f (delta: %+.2f)", self._paper_balance, pnl)

    @property
    def balance(self) -> float:
        return self._paper_balance if TRADING_MODE == "paper" else self._balance

    # ------------------------------------------------------------------
    # PnL resolution
    # ------------------------------------------------------------------

    def record_trade_pnl(
        self,
        trade_id: str,
        timestamp: str,
        resolved_yes: bool,
        cost_per_contract: float,  # ask_price in dollars
        count: int,
    ) -> float:
        """
        Computes and records PnL for a resolved trade.

        YES resolved: pnl = (1.0 - cost_per_contract) * count
        NO resolved:  pnl = -cost_per_contract * count
        """
        if resolved_yes:
            pnl = (1.0 - cost_per_contract) * count
        else:
            pnl = -cost_per_contract * count

        try:
            self.db.mark_trade_resolved(trade_id, timestamp, resolved_yes, pnl)
        except Exception as e:
            logger.error("Failed to mark trade resolved in DB: %s", e)

        if TRADING_MODE == "paper":
            self.adjust_paper_balance(pnl)

        result_str = "WON" if resolved_yes else "LOST"
        logger.info(
            "Trade resolved: %s | %s | pnl=%+.2f | id=%s",
            result_str,
            "YES" if resolved_yes else "NO",
            pnl,
            trade_id[:8],
        )
        return pnl

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_win_rate(self, lookback_days: int = 30) -> Optional[float]:
        """Returns win_count / total_resolved for last N days, or None if no data."""
        cutoff = (datetime.date.today() - datetime.timedelta(days=lookback_days)).isoformat()
        try:
            all_pnl = self.db.get_all_daily_pnl()
        except Exception as e:
            logger.error("Failed to fetch daily PnL: %s", e)
            return None

        win_total = 0
        loss_total = 0
        for day in all_pnl:
            if day["date"] >= cutoff:
                win_total += day.get("win_count", 0)
                loss_total += day.get("loss_count", 0)

        total = win_total + loss_total
        if total == 0:
            return None
        return win_total / total

    def get_daily_summary(self, date: Optional[datetime.date] = None) -> dict:
        """Returns today's or specified date's PnL summary."""
        if date is None:
            date = datetime.date.today()
        date_str = date.isoformat()

        try:
            pnl_record = self.db.get_daily_pnl(date_str)
        except Exception:
            pnl_record = None

        trades = []
        try:
            trades = self.db.get_daily_trades(date_str)
        except Exception:
            pass

        open_count = sum(1 for t in trades if not t.get("resolved", True))
        resolved = [t for t in trades if t.get("resolved", False)]
        wins = sum(1 for t in resolved if t.get("resolved_yes", False))
        losses = len(resolved) - wins

        return {
            "date": date_str,
            "balance": self.balance,
            "open_positions": open_count,
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / len(resolved) if resolved else None,
            "realized_pnl": pnl_record.get("realized_pnl", 0.0) if pnl_record else None,
            "mode": TRADING_MODE,
        }

    def record_daily_snapshot(self) -> None:
        """Save today's PnL snapshot to DynamoDB. Called at end of day."""
        today = datetime.date.today().isoformat()
        summary = self.get_daily_summary()

        try:
            self.db.put_daily_pnl(
                date_str=today,
                starting_balance=STARTING_BALANCE,
                ending_balance=self.balance,
                realized_pnl=summary.get("realized_pnl") or 0.0,
                win_count=summary["wins"],
                loss_count=summary["losses"],
            )
            logger.info(
                "Daily snapshot saved: date=%s balance=%.2f wins=%d losses=%d",
                today, self.balance, summary["wins"], summary["losses"],
            )
        except Exception as e:
            logger.error("Failed to save daily snapshot: %s", e)

    def compute_compounded_returns(self) -> dict:
        """Returns compound growth stats since start."""
        try:
            all_pnl = self.db.get_all_daily_pnl()
        except Exception:
            all_pnl = []

        if not all_pnl:
            return {
                "total_return_pct": 0.0,
                "daily_avg_pct": 0.0,
                "days_running": 0,
            }

        all_pnl.sort(key=lambda d: d["date"])
        first_balance = all_pnl[0]["starting_balance"] or STARTING_BALANCE
        current = self.balance
        total_return = (current - first_balance) / first_balance if first_balance > 0 else 0.0
        days = (datetime.date.today() - self._start_date).days or 1

        return {
            "total_return_pct": total_return * 100,
            "daily_avg_pct": total_return / days * 100,
            "days_running": days,
        }
