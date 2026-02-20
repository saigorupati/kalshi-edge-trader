"""
FastAPI Dashboard API Server
============================
Runs in a background daemon thread alongside main.py's APScheduler loop.
Shares _db, _kalshi, _risk, _tracker by reference — no IPC needed.

_scanner_state is updated by main.py calling update_scanner_state() each cycle.
WebSocket clients receive push updates on each cycle + 10s heartbeats.

Start from main.py:
    from api.server import start_api_server, update_scanner_state
    start_api_server(db=_db, kalshi=_kalshi, risk=_risk, tracker=_tracker)
"""

import asyncio
import datetime
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from config import CITIES, TRADING_MODE, STARTING_BALANCE, MAX_POSITION_PCT_PER_CITY, MAX_OPEN_POSITIONS, DAILY_STOP_LOSS_PCT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state — injected by main.py via inject_state()
# ---------------------------------------------------------------------------
_db = None
_kalshi = None
_risk = None
_tracker = None

_scanner_state: Dict[str, Any] = {
    "last_updated": None,
    "cycle_number": 0,
    "opportunities": {},
    "dist_by_city": {},
}

_ws_clients: List[WebSocket] = []
_event_loop: Optional[asyncio.AbstractEventLoop] = None


def inject_state(db, kalshi, risk, tracker) -> None:
    global _db, _kalshi, _risk, _tracker
    _db = db
    _kalshi = kalshi
    _risk = risk
    _tracker = tracker
    logger.info("API server state injected.")


def update_scanner_state(
    opportunities_by_city: dict,
    dist_by_city: dict,
    cycle_number: int,
) -> None:
    """
    Called by main.py's trading_cycle() at the end of each cycle.
    Serializes TradeOpportunity and TempDistribution objects to dicts.
    Also broadcasts to all connected WebSocket clients.
    """
    global _scanner_state

    opps_serialized = {}
    for city_code, opps in opportunities_by_city.items():
        opps_serialized[city_code] = [
            {
                "ticker": o.market.ticker,
                "temp_range": o.market.yes_sub_title,
                "model_prob": round(o.model_prob, 4),
                "ask_price": round(o.ask_price, 4),
                "bid_price": round(o.bid_price, 4),
                "spread": round(o.spread, 4),
                "raw_edge": round(o.raw_edge, 4),
                "net_edge": round(o.net_edge, 4),
                "has_edge": o.has_edge,
                "ev_per_dollar": round(o.ev_per_dollar, 4),
            }
            for o in opps[:12]
        ]

    dists_serialized = {}
    for city_code, dist in dist_by_city.items():
        dists_serialized[city_code] = {
            "mu": round(dist.mu, 1),
            "sigma": round(dist.sigma, 1),
            "raw_mu": round(dist.raw_mu, 1),
            "raw_sigma": round(dist.raw_sigma, 1),
            "bias_applied": round(dist.bias_applied, 2),
            "valid_date": dist.valid_date,
        }

    _scanner_state = {
        "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cycle_number": cycle_number,
        "opportunities": opps_serialized,
        "dist_by_city": dists_serialized,
    }

    # Push to WebSocket clients if event loop is running
    if _event_loop and not _event_loop.is_closed():
        asyncio.run_coroutine_threadsafe(_broadcast_live_update(), _event_loop)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Kalshi Edge Trader", version="1.0.0", docs_url="/api/docs")

FRONTEND_URL = os.getenv("FRONTEND_URL", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL] if FRONTEND_URL != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_trade(trade: dict) -> dict:
    return {
        "trade_id": trade["trade_id"],
        "timestamp": trade["timestamp"],
        "trade_date": trade["trade_date"],
        "city": trade["city"],
        "ticker": trade["ticker"],
        "side": trade.get("side", "yes"),
        "action": trade.get("action", "buy"),
        "count": trade["count"],
        "price_cents": trade["price_cents"],
        "entry_price": round(trade["price_cents"] / 100.0, 4),
        "model_prob": trade.get("model_prob"),
        "edge": trade.get("edge"),
        "kelly_fraction": trade.get("kelly_fraction"),
        "dollar_risk": trade.get("dollar_risk"),
        "mode": trade["mode"],
        "order_id": trade.get("order_id", ""),
        "resolved": trade.get("resolved", False),
        "resolved_yes": trade.get("resolved_yes"),
        "pnl": trade.get("pnl"),
    }


def _compute_unrealized_pnl(trade: dict) -> Optional[float]:
    """Estimate unrealized P&L by fetching the current orderbook bid."""
    if _kalshi is None:
        return None
    try:
        ob = _kalshi.get_orderbook(trade["ticker"], depth=3)
        if ob is None:
            return None
        current_bid = ob.best_bid()
        if current_bid is None:
            return None
        entry_price = trade["price_cents"] / 100.0
        count = trade["count"]
        return round((current_bid - entry_price) * count, 4)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "mode": TRADING_MODE,
        "bot_initialized": _db is not None,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


@app.get("/api/balance")
async def get_balance():
    if _kalshi is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    try:
        balance = _kalshi.get_balance()
        return {
            "balance": round(balance, 2),
            "mode": TRADING_MODE,
            "starting_balance": STARTING_BALANCE,
            "total_return_pct": round(
                (balance - STARTING_BALANCE) / STARTING_BALANCE * 100, 2
            ) if STARTING_BALANCE > 0 else 0.0,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/open")
async def get_open_positions():
    if _db is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    try:
        trades = _db.get_open_trades()
        positions = []
        for trade in trades:
            s = _serialize_trade(trade)
            s["unrealized_pnl"] = _compute_unrealized_pnl(trade)
            city_cfg = CITIES.get(trade["city"])
            s["city_display"] = city_cfg.display_name if city_cfg else trade["city"]
            positions.append(s)
        return {"positions": positions, "count": len(positions)}
    except Exception as e:
        logger.error("Error fetching open positions: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trades")
async def get_trades(date: Optional[str] = None, city: Optional[str] = None):
    if _db is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    if date is None:
        date = datetime.date.today().isoformat()
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date. Use YYYY-MM-DD.")
    try:
        trades = _db.get_daily_trades(date, city=city)
        return {
            "date": date,
            "city": city,
            "trades": [_serialize_trade(t) for t in trades],
            "count": len(trades),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pnl/today")
async def get_pnl_today():
    if _db is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    try:
        today = datetime.date.today().isoformat()
        trades = _db.get_daily_trades(today)
        resolved = [t for t in trades if t.get("resolved", False)]
        wins = sum(1 for t in resolved if t.get("resolved_yes", False))
        losses = len(resolved) - wins
        open_count = sum(1 for t in trades if not t.get("resolved", False))
        realized = sum(
            t.get("pnl") or 0.0 for t in resolved if t.get("pnl") is not None
        )
        stored = _db.get_daily_pnl(today)
        return {
            "date": today,
            "realized_pnl": round(realized, 2),
            "win_count": wins,
            "loss_count": losses,
            "open_positions": open_count,
            "total_trades": len(trades),
            "win_rate": round(wins / len(resolved), 4) if resolved else None,
            "stored_snapshot": stored,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pnl/history")
async def get_pnl_history():
    if _db is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    try:
        records = _db.get_all_daily_pnl()
        records.sort(key=lambda r: r["date"])
        return {"history": records, "count": len(records)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/risk/status")
async def get_risk_status():
    if _risk is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    status = _risk.status_summary()
    balance = status.get("day_start_balance", STARTING_BALANCE)
    city_details = {}
    for city_code, cfg in CITIES.items():
        exposure = status["city_exposure"].get(city_code, 0.0)
        budget = MAX_POSITION_PCT_PER_CITY * balance
        city_details[city_code] = {
            "display_name": cfg.display_name,
            "exposure": round(exposure, 2),
            "budget": round(budget, 2),
            "pct_used": round(exposure / budget * 100, 1) if budget > 0 else 0.0,
        }
    return {
        "kill_switch_active": status["kill_switch"],
        "open_positions": status["open_positions"],
        "max_positions": MAX_OPEN_POSITIONS,
        "day_start_balance": round(status["day_start_balance"], 2),
        "daily_stop_loss_pct": DAILY_STOP_LOSS_PCT * 100,
        "stop_loss_threshold": round(
            status["day_start_balance"] * (1 - DAILY_STOP_LOSS_PCT), 2
        ),
        "city_exposure": city_details,
        "mode": TRADING_MODE,
    }


@app.get("/api/markets/{city_code}")
async def get_city_markets(city_code: str):
    city_code = city_code.upper()
    city_cfg = CITIES.get(city_code)
    if city_cfg is None:
        raise HTTPException(status_code=404, detail=f"Unknown city: {city_code}")
    if _kalshi is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    try:
        markets = _kalshi.get_city_markets(city_cfg.kalshi_series)
        return {
            "city": city_code,
            "display_name": city_cfg.display_name,
            "markets": [
                {
                    "ticker": m.ticker,
                    "yes_sub_title": m.yes_sub_title,
                    "yes_ask": m.yes_ask,
                    "yes_bid": m.yes_bid,
                    "temp_low": m.temp_low,
                    "temp_high": m.temp_high,
                    "is_open_low": m.is_open_low,
                    "is_open_high": m.is_open_high,
                    "volume": m.volume,
                    "status": m.status,
                }
                for m in markets
            ],
            "count": len(markets),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/calibration/{city_code}")
async def get_calibration(city_code: str, days: int = 30):
    city_code = city_code.upper()
    if city_code not in CITIES:
        raise HTTPException(status_code=404, detail=f"Unknown city: {city_code}")
    if _db is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    try:
        history = _db.get_calibration_history(city_code, lookback_days=days)
        cfg = CITIES[city_code]
        return {
            "city": city_code,
            "display_name": cfg.display_name,
            "bias_correction": cfg.bias_correction,
            "sigma_scale": cfg.sigma_scale,
            "lookback_days": days,
            "records": history,
            "count": len(history),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/scanner")
async def get_scanner():
    return _scanner_state


@app.delete("/api/orders/{order_id}")
async def cancel_order(order_id: str, trade_id: Optional[str] = None):
    """
    Cancels a Kalshi order. Optionally marks the DynamoDB trade as resolved.
    In paper mode, cancels the mock position in DynamoDB only.
    """
    if _kalshi is None:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    try:
        result = _kalshi.cancel_order(order_id)
        response: dict = {
            "order_id": order_id,
            "cancel_result": result,
            "trade_resolved": False,
        }
        if trade_id and _db is not None:
            open_trades = _db.get_open_trades()
            matched = next((t for t in open_trades if t["trade_id"] == trade_id), None)
            if matched:
                _db.mark_trade_resolved(
                    trade_id=matched["trade_id"],
                    timestamp=matched["timestamp"],
                    resolved_yes=False,
                    pnl=0.0,
                )
                if _risk is not None:
                    _risk.close_position(matched["city"], matched.get("dollar_risk", 0.0))
                response["trade_resolved"] = True
                response["trade_id"] = trade_id
        return response
    except Exception as e:
        logger.error("Failed to cancel order %s: %s", order_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# WebSocket live feed
# ---------------------------------------------------------------------------

async def _broadcast_live_update():
    """Push scanner + risk snapshot to all connected WebSocket clients."""
    if not _ws_clients:
        return
    try:
        payload = {
            "type": "live_update",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "scanner": _scanner_state,
            "risk": _risk.status_summary() if _risk else None,
        }
        data = json.dumps(payload)
        dead = []
        for ws in list(_ws_clients):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in _ws_clients:
                _ws_clients.remove(ws)
    except Exception as e:
        logger.error("WebSocket broadcast error: %s", e)


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.append(websocket)
    logger.info("WebSocket client connected. Total: %d", len(_ws_clients))
    try:
        # Initial snapshot on connect
        initial = {
            "type": "initial",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "scanner": _scanner_state,
            "risk": _risk.status_summary() if _risk else None,
        }
        await websocket.send_text(json.dumps(initial))

        # Heartbeat every 10 seconds
        while True:
            await asyncio.sleep(10)
            heartbeat = {
                "type": "heartbeat",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "risk": _risk.status_summary() if _risk else None,
            }
            await websocket.send_text(json.dumps(heartbeat))
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as e:
        logger.error("WebSocket error: %s", e)
    finally:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

def start_api_server(
    db,
    kalshi,
    risk,
    tracker,
    host: str = "0.0.0.0",
    port: int = 8000,
) -> threading.Thread:
    """
    Starts the FastAPI server in a background daemon thread.
    Stores the asyncio event loop globally so trading_cycle() can broadcast
    WebSocket updates via asyncio.run_coroutine_threadsafe().
    """
    global _event_loop
    inject_state(db, kalshi, risk, tracker)

    def _run():
        global _event_loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _event_loop = loop
        config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            loop="none",
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        loop.run_until_complete(server.serve())

    thread = threading.Thread(target=_run, daemon=True, name="api-server")
    thread.start()
    logger.info("FastAPI dashboard API started at http://%s:%d | docs: /api/docs", host, port)
    return thread
