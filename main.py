"""
Kalshi Temperature Edge Trading Bot — Main Entry Point

Runs a 30-minute scheduling loop that:
1. Downloads NBM probabilistic forecasts for all 5 cities
2. Fetches Kalshi market prices for tomorrow's temperature contracts
3. Computes model edge vs market price using calibrated Normal distribution
4. Places orders where edge > 5% (paper/demo/live mode)
5. Logs everything to AWS DynamoDB

Usage:
  TRADING_MODE=paper python main.py
  TRADING_MODE=demo  python main.py
  TRADING_MODE=live  python main.py

Environment variables: see .env.example
"""

import os
import signal
import sys
import logging
import datetime
from typing import Dict, List

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from config import (
    CITIES,
    TRADING_MODE,
    LOOP_INTERVAL_MINUTES,
    STARTING_BALANCE,
    LOG_LEVEL,
)
from db.dynamo import DynamoClient
from data.weather import fetch_all_city_forecasts, get_nws_forecast_high
from data.kalshi import KalshiClient
from models.temperature import fit_normal_from_nbm
from models.calibration import (
    update_city_calibration,
    store_forecast_calibration,
    fill_actual_highs,
)
from trading.edge import find_opportunities, filter_viable_opportunities
from trading.executor import TradeExecutor
from trading.risk import RiskManager
from portfolio.tracker import PortfolioTracker
from dashboard import print_cycle_report, log_cycle_summary
from api.server import start_api_server, update_scanner_state

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_cycle_count = 0
_db: DynamoClient = None
_kalshi: KalshiClient = None
_risk: RiskManager = None
_tracker: PortfolioTracker = None
_executor: TradeExecutor = None


def trading_cycle() -> None:
    """
    One full 30-minute trading cycle. Runs in APScheduler background thread.
    """
    global _cycle_count
    _cycle_count += 1
    logger.info("=" * 60)
    logger.info("Starting trading cycle #%d | mode=%s", _cycle_count, TRADING_MODE)

    # --- Daily reset check ---
    today = datetime.date.today()
    if _risk._today != today:
        balance = _tracker.sync_balance()
        _risk.reset_daily(balance)
        _tracker.record_daily_snapshot()  # Save yesterday's snapshot first

    # --- Sync balance ---
    try:
        balance = _tracker.sync_balance()
        _executor.update_balance(balance)
        _risk.update_balance(balance)
    except Exception as e:
        logger.error("Balance sync failed: %s", e)
        balance = _risk._current_balance

    # --- Kill switch check ---
    if _risk.check_kill_switch(balance):
        logger.warning("Kill switch active — skipping cycle #%d", _cycle_count)
        return

    # --- Fetch NBM forecasts (one 33MB download for all 5 cities) ---
    logger.info("Fetching NBM forecasts...")
    try:
        nbm_forecasts = fetch_all_city_forecasts(CITIES)
    except Exception as e:
        logger.error("NBM fetch failed: %s — skipping cycle", e)
        return

    if not nbm_forecasts:
        logger.error("No NBM forecasts returned — skipping cycle")
        return

    # --- Process each city ---
    dist_by_city = {}
    opps_by_city = {}
    executed_by_city: Dict[str, list] = {}

    for city_code, city_cfg in CITIES.items():
        forecast = nbm_forecasts.get(city_code)
        if forecast is None:
            logger.warning("No NBM forecast for %s — skipping", city_code)
            continue

        # NWS sanity check (non-blocking)
        nws_high = None
        try:
            nws_high = get_nws_forecast_high(city_cfg)
        except Exception:
            pass

        # Store calibration data for future model improvement
        store_forecast_calibration(_db, city_code, forecast, nws_high)

        # Fit calibrated Normal distribution
        dist = fit_normal_from_nbm(forecast, city_cfg)
        dist_by_city[city_code] = dist

        logger.info(
            "%s: mu=%.1f°F sigma=%.1f°F (nws_check=%.0f°F)",
            city_code, dist.mu, dist.sigma, nws_high or 0,
        )

        # Fetch Kalshi markets for tomorrow
        try:
            markets = _kalshi.get_city_markets(city_cfg.kalshi_series)
        except Exception as e:
            logger.error("Failed to fetch Kalshi markets for %s: %s", city_code, e)
            continue

        if not markets:
            logger.info("%s: No open markets found for tomorrow", city_code)
            continue

        # Find and filter opportunities
        all_opps = find_opportunities(dist, markets, _kalshi, city_code)
        viable = filter_viable_opportunities(all_opps)
        opps_by_city[city_code] = all_opps  # store all for dashboard

        if not viable:
            logger.info("%s: No viable opportunities (edge threshold not met)", city_code)
            continue

        logger.info(
            "%s: %d viable opportunities | best=%.1f%% on %s",
            city_code,
            len(viable),
            viable[0].net_edge * 100,
            viable[0].market.ticker,
        )

        # Execute the best opportunity
        executed = _executor.execute_city_opportunities(city_code, viable)
        executed_by_city[city_code] = executed

    # --- Dashboard output ---
    try:
        print_cycle_report(
            opps_by_city, dist_by_city, executed_by_city,
            _tracker, _risk, _cycle_count,
        )
        log_cycle_summary(
            opps_by_city, dist_by_city, executed_by_city,
            balance, _cycle_count,
        )
    except Exception as e:
        logger.error("Dashboard error: %s", e)

    # --- Push scanner results to API server (WebSocket broadcast) ---
    try:
        update_scanner_state(opps_by_city, dist_by_city, _cycle_count)
    except Exception as e:
        logger.error("Failed to update scanner state: %s", e)

    logger.info("Cycle #%d complete.", _cycle_count)


def daily_calibration_update() -> None:
    """Called daily at 09:00 to update model calibration from DynamoDB history."""
    logger.info("Running daily calibration update...")
    try:
        fill_actual_highs(_db, lambda city_cfg: get_nws_forecast_high(city_cfg))
        update_city_calibration(_db)
        logger.info("Calibration update complete.")
    except Exception as e:
        logger.error("Calibration update failed: %s", e)


def daily_pnl_snapshot() -> None:
    """Called at 23:55 to save end-of-day PnL snapshot."""
    try:
        _tracker.record_daily_snapshot()
    except Exception as e:
        logger.error("Daily PnL snapshot failed: %s", e)


def initialize() -> None:
    """Initialize all components."""
    global _db, _kalshi, _risk, _tracker, _executor

    logger.info("Initializing Kalshi Edge Trader | mode=%s", TRADING_MODE)

    # ── Validate required env vars before any network calls ──────────
    missing = []
    import os as _os
    if not _os.getenv("AWS_ACCESS_KEY_ID"):
        missing.append("AWS_ACCESS_KEY_ID")
    if not _os.getenv("AWS_SECRET_ACCESS_KEY"):
        missing.append("AWS_SECRET_ACCESS_KEY")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Go to Railway → your bot service → Variables and add them."
        )

    # DynamoDB
    _db = DynamoClient()
    _db.ensure_tables_exist()

    # Kalshi client
    _kalshi = KalshiClient()

    # Balance
    if TRADING_MODE == "paper":
        balance = STARTING_BALANCE
    else:
        try:
            balance = _kalshi.get_balance()
            logger.info("Kalshi balance: $%.2f", balance)
        except Exception as e:
            logger.error("Could not fetch balance: %s — using default", e)
            balance = STARTING_BALANCE

    # Risk manager
    _risk = RiskManager(balance)

    # Rebuild open positions from DynamoDB (handles container restarts)
    try:
        open_trades = _db.get_open_trades()
        _risk.rebuild_from_open_trades(open_trades)
    except Exception as e:
        logger.warning("Could not rebuild risk state from DynamoDB: %s", e)

    # Portfolio tracker
    _tracker = PortfolioTracker(_db, _kalshi)
    _tracker._balance = balance
    _tracker._paper_balance = balance

    # Trade executor
    _executor = TradeExecutor(_kalshi, _risk, _db, balance)

    # Calibration: update on startup if enough history exists
    try:
        update_city_calibration(_db)
    except Exception as e:
        logger.warning("Initial calibration update skipped: %s", e)

    # Start FastAPI dashboard server in background thread
    start_api_server(
        db=_db,
        kalshi=_kalshi,
        risk=_risk,
        tracker=_tracker,
        host="0.0.0.0",
        port=int(os.getenv("API_PORT", "8000")),
    )

    logger.info("Initialization complete.")


def main() -> None:
    initialize()

    # --- APScheduler ---
    scheduler = BackgroundScheduler(
        job_defaults={
            "misfire_grace_time": 120,
            "coalesce": True,
            "max_instances": 1,
        }
    )

    # Main trading loop every 30 minutes
    scheduler.add_job(
        func=trading_cycle,
        trigger=IntervalTrigger(minutes=LOOP_INTERVAL_MINUTES),
        id="trading_cycle",
    )

    # Daily calibration update at 09:00 local
    scheduler.add_job(
        func=daily_calibration_update,
        trigger=CronTrigger(hour=9, minute=0),
        id="daily_calibration",
    )

    # Daily PnL snapshot at 23:55
    scheduler.add_job(
        func=daily_pnl_snapshot,
        trigger=CronTrigger(hour=23, minute=55),
        id="daily_pnl",
    )

    scheduler.start()
    logger.info(
        "Scheduler started. Trading every %d minutes. Mode: %s",
        LOOP_INTERVAL_MINUTES, TRADING_MODE,
    )

    # Run first cycle immediately
    logger.info("Running initial cycle...")
    trading_cycle()

    # Graceful shutdown handler
    def shutdown(signum, frame):
        logger.info("Shutdown signal received. Stopping scheduler...")
        scheduler.shutdown(wait=False)
        try:
            _tracker.record_daily_snapshot()
        except Exception:
            pass
        logger.info("Shutdown complete.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep main thread alive (scheduler runs in background thread)
    import time
    while True:
        time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except EnvironmentError as exc:
        logger.critical("=" * 60)
        logger.critical("STARTUP FAILED — configuration error:")
        logger.critical("%s", exc)
        logger.critical("=" * 60)
        sys.exit(1)
    except Exception as exc:
        logger.critical("STARTUP FAILED — unexpected error: %s", exc, exc_info=True)
        sys.exit(1)
