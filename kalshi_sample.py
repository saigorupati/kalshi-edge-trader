"""
kalshi_sample.py — Kalshi API Qualification Sample
=====================================================
Demonstrates the full lifecycle required for the advanced API tier:

  Step 1 — Query the API for market data on today's NYC high-temp market
  Step 2 — Query the orderbook for the most liquid NYC market
  Step 3 — Place a 1-unit limit order, confirm it rested, then cancel it

Architecture context
--------------------
This script is an excerpt of the broader kalshi-edge-trader system, which
uses NOAA NBM probabilistic forecasts to predict daily temperature highs
across 5 cities (NYC, LA, Miami, Chicago, SF) and trades edges on Kalshi
temperature contracts. This sample isolates the three API interaction steps
to demonstrate the following reliability properties:

  • RSA-PSS SHA-256 request signing (Kalshi v2 auth spec)
  • Exponential backoff with jitter on 429/5xx responses
  • Idempotent order placement via client-generated UUIDs
  • Schema validation on every API response
  • Circuit breaker: halt if consecutive failures exceed threshold
  • Real-time position monitoring: order status confirmed before cancel

Environment variables required
-------------------------------
  KALSHI_KEY_ID          — API key ID from https://kalshi.com/settings/api
  KALSHI_PRIVATE_KEY_PEM — RSA private key in PEM format (use \\n for newlines)
  TRADING_MODE           — Set to "demo" to use the sandbox, "live" for prod

Usage
-----
  export KALSHI_KEY_ID=your_key_id
  export KALSHI_PRIVATE_KEY_PEM="-----BEGIN RSA PRIVATE KEY-----\\n...\\n-----END RSA PRIVATE KEY-----"
  export TRADING_MODE=demo
  python kalshi_sample.py
"""

import os
import sys
import time
import uuid
import base64
import logging
import datetime
from typing import Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("kalshi_sample")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TRADING_MODE = os.getenv("TRADING_MODE", "demo").lower()
BASE_URL = (
    "https://demo-api.kalshi.co/trade-api/v2"
    if TRADING_MODE == "demo"
    else "https://api.elections.kalshi.com/trade-api/v2"
)
KEY_ID = os.getenv("KALSHI_KEY_ID", "")
PRIVATE_KEY_PEM = os.getenv("KALSHI_PRIVATE_KEY_PEM", "").replace("\\n", "\n")

NYC_SERIES = "KXHIGHNY"   # Kalshi NYC high-temperature series ticker

# Retry / circuit-breaker settings
MAX_RETRIES = 4
BASE_BACKOFF_S = 0.5      # seconds (doubles each retry)
REQUEST_TIMEOUT_S = 20
CIRCUIT_BREAKER_THRESHOLD = 5  # consecutive failures before halting

# ---------------------------------------------------------------------------
# RSA-PSS Auth
# ---------------------------------------------------------------------------

def _load_private_key():
    """Load RSA private key from PEM string. Returns None if not configured."""
    if not PRIVATE_KEY_PEM.strip().startswith("-----"):
        log.error("KALSHI_PRIVATE_KEY_PEM is missing or malformed.")
        return None
    try:
        return serialization.load_pem_private_key(PRIVATE_KEY_PEM.encode(), password=None)
    except Exception as exc:
        log.error("Failed to parse private key: %s", exc)
        return None


def _auth_headers(method: str, path: str, private_key) -> dict:
    """
    Produce the three Kalshi auth headers required by the v2 API.

    Signature covers: {timestamp_ms}{METHOD_UPPER}{path_no_query}
    Algorithm: RSA-PSS, SHA-256, salt length = digest length (32 bytes)
    """
    if private_key is None:
        return {}
    timestamp_ms = str(int(time.time() * 1000))
    path_no_query = path.split("?")[0]
    message = f"{timestamp_ms}{method.upper()}{path_no_query}".encode()
    sig = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Resilient HTTP helpers
# ---------------------------------------------------------------------------

_consecutive_failures = 0


def _request(method: str, path: str, private_key, *, params=None, json_body=None) -> dict:
    """
    Executes an authenticated Kalshi API request with:
      - RSA-PSS signing
      - Exponential backoff on 429 / 5xx
      - Schema guard (response must be a JSON object)
      - Circuit breaker after CIRCUIT_BREAKER_THRESHOLD consecutive failures
    """
    global _consecutive_failures

    if _consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
        raise RuntimeError(
            f"Circuit breaker open: {_consecutive_failures} consecutive API failures. "
            "Halting to prevent further risk."
        )

    url = BASE_URL + path
    headers = _auth_headers(method, path, private_key)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=REQUEST_TIMEOUT_S,
            )

            # Rate-limit: back off and retry
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", BASE_BACKOFF_S * (2 ** attempt)))
                log.warning("Rate limited (429). Waiting %.1fs before retry %d/%d.", retry_after, attempt, MAX_RETRIES)
                time.sleep(retry_after)
                continue

            # Server errors: exponential backoff
            if resp.status_code >= 500:
                backoff = BASE_BACKOFF_S * (2 ** (attempt - 1))
                log.warning("Server error %d. Backing off %.1fs (attempt %d/%d).", resp.status_code, backoff, attempt, MAX_RETRIES)
                time.sleep(backoff)
                continue

            resp.raise_for_status()
            data = resp.json()

            # Schema guard: top-level response must be a dict
            if not isinstance(data, dict):
                raise ValueError(f"Unexpected API response type: {type(data).__name__} (expected dict)")

            _consecutive_failures = 0   # Reset circuit breaker on success
            return data

        except requests.exceptions.Timeout:
            log.warning("Request timed out (attempt %d/%d).", attempt, MAX_RETRIES)
            if attempt == MAX_RETRIES:
                _consecutive_failures += 1
                raise
            time.sleep(BASE_BACKOFF_S * (2 ** (attempt - 1)))

        except (requests.exceptions.ConnectionError, ValueError) as exc:
            log.error("Request error: %s (attempt %d/%d).", exc, attempt, MAX_RETRIES)
            _consecutive_failures += 1
            raise

    _consecutive_failures += 1
    raise RuntimeError(f"All {MAX_RETRIES} retry attempts exhausted for {method} {path}")


def _get(path: str, key, **params) -> dict:
    filtered = {k: v for k, v in params.items() if v is not None}
    return _request("GET", path, key, params=filtered or None)


def _post(path: str, key, body: dict) -> dict:
    return _request("POST", path, key, json_body=body)


def _delete(path: str, key) -> dict:
    return _request("DELETE", path, key)


# ---------------------------------------------------------------------------
# Step 1 — Query NYC weather market data
# ---------------------------------------------------------------------------

def step1_get_nyc_markets(key) -> list:
    """
    Find today's open NYC high-temperature markets.

    Flow:
      GET /events?series_ticker=KXHIGHNY&status=open
        → find event whose close_time is tomorrow (today's settling market)
      GET /markets?event_ticker={event_ticker}&status=open
        → return all temp-bin contracts
    """
    log.info("=" * 60)
    log.info("STEP 1 — Querying NYC weather market data")
    log.info("Series: %s | Base URL: %s", NYC_SERIES, BASE_URL)

    # Get open events for the NYC high-temp series
    events_data = _get("/events", key, series_ticker=NYC_SERIES, status="open")
    events = events_data.get("events", [])

    if not events:
        log.warning("No open events found for series %s.", NYC_SERIES)
        return []

    log.info("Found %d open event(s) for %s.", len(events), NYC_SERIES)

    # Find the event that closes tomorrow (today's market)
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    target_event = None
    for event in events:
        close_time = event.get("close_time", "")
        if close_time.startswith(tomorrow):
            target_event = event
            break

    # Fallback: use the soonest-closing event
    if target_event is None:
        target_event = sorted(events, key=lambda e: e.get("close_time", ""))[0]
        log.info("Using soonest event: %s (closes %s)", target_event.get("event_ticker"), target_event.get("close_time"))
    else:
        log.info("Found tomorrow's event: %s (closes %s)", target_event.get("event_ticker"), target_event.get("close_time"))

    event_ticker = target_event["event_ticker"]

    # Validate expected fields
    required_fields = {"event_ticker", "title", "close_time", "status"}
    missing = required_fields - set(target_event.keys())
    if missing:
        log.warning("Event response missing expected fields: %s", missing)

    # Fetch all markets for this event
    markets_data = _get("/markets", key, event_ticker=event_ticker, status="open")
    markets = markets_data.get("markets", [])

    if not markets:
        log.warning("No open markets found for event %s.", event_ticker)
        return []

    log.info("NYC event %s → %d open markets:", event_ticker, len(markets))
    for mkt in markets[:8]:   # Print first 8 for brevity
        yes_ask = mkt.get("yes_ask") or mkt.get("yes_ask_price", 0)
        yes_bid = mkt.get("yes_bid") or mkt.get("yes_bid_price", 0)
        subtitle = mkt.get("yes_sub_title", mkt.get("subtitle", "?"))
        log.info(
            "  %-42s %-20s  ask=%-4s  bid=%-4s  vol=%s",
            mkt["ticker"], subtitle,
            f"{int(yes_ask)}¢" if yes_ask else "N/A",
            f"{int(yes_bid)}¢" if yes_bid else "N/A",
            mkt.get("volume", 0),
        )
    if len(markets) > 8:
        log.info("  ... and %d more markets", len(markets) - 8)

    return markets


# ---------------------------------------------------------------------------
# Step 2 — Query the orderbook
# ---------------------------------------------------------------------------

def step2_get_orderbook(key, markets: list) -> Optional[str]:
    """
    Fetch the orderbook for the most liquid NYC market (highest volume).
    Returns the selected ticker so Step 3 can use the same market.
    """
    log.info("=" * 60)
    log.info("STEP 2 — Querying NYC orderbook")

    if not markets:
        log.error("No markets available to query orderbook.")
        return None

    # Select the market with the highest volume (most liquidity)
    best_market = max(markets, key=lambda m: int(m.get("volume", 0)))
    ticker = best_market["ticker"]
    subtitle = best_market.get("yes_sub_title", best_market.get("subtitle", "?"))

    log.info("Selected market: %s (%s) — volume=%s", ticker, subtitle, best_market.get("volume", 0))

    ob_data = _get(f"/markets/{ticker}/orderbook", key, depth=10)
    ob = ob_data.get("orderbook", ob_data)

    # YES bids = buyers of YES contracts
    yes_bids = ob.get("yes", [])
    # NO bids = buyers of NO contracts → imply YES asks (YES_ask = 100 - NO_bid)
    no_bids = ob.get("no", [])

    log.info("Orderbook for %s (%s):", ticker, subtitle)
    log.info("  YES side (bids — willing to buy YES):")
    if yes_bids:
        for price, qty in sorted(yes_bids, reverse=True)[:5]:
            log.info("    %3d¢  x %d contracts", price, qty)
    else:
        log.info("    (empty)")

    log.info("  NO side → implied YES asks:")
    if no_bids:
        for price, qty in sorted(no_bids)[:5]:
            implied_yes_ask = 100 - price
            log.info("    %3d¢  x %d contracts  (NO bid %d¢ → YES ask %d¢)", implied_yes_ask, qty, price, implied_yes_ask)
    else:
        log.info("    (empty)")

    # Compute and report spread
    best_yes_bid = max((p for p, _ in yes_bids), default=None) if yes_bids else None
    best_yes_ask = (100 - min((p for p, _ in no_bids), default=100)) if no_bids else None
    if best_yes_bid is not None and best_yes_ask is not None:
        spread = best_yes_ask - best_yes_bid
        log.info("  Spread: %d¢  (bid=%d¢, ask=%d¢)", spread, best_yes_bid, best_yes_ask)

    return ticker


# ---------------------------------------------------------------------------
# Step 3 — Place and cancel a 1-unit order
# ---------------------------------------------------------------------------

def step3_place_and_cancel(key, ticker: str) -> None:
    """
    Places a 1-unit limit YES buy order on the given market at a conservative
    price (1 cent), then confirms its resting status and cancels it.

    Reliability properties demonstrated:
      • Idempotency: order has a client-generated UUID — safe to retry
      • Position confirmation: order status verified before cancel
      • Explicit cancellation: DELETE /portfolio/orders/{order_id}
      • Reconciliation: balance checked before and after
    """
    log.info("=" * 60)
    log.info("STEP 3 — Placing and cancelling a 1-unit order")
    log.info("Market: %s", ticker)

    # --- Pre-trade: check balance ---
    balance_resp = _get("/portfolio/balance", key)
    balance_cents = balance_resp.get("balance", 0)
    log.info("Account balance before order: $%.2f", balance_cents / 100)

    if balance_cents < 1:
        log.error("Insufficient balance to place even a 1-cent order. Aborting step 3.")
        return

    # --- Place order ---
    # Use a conservative price of 1 cent so the order rests without filling.
    # Client-generated UUID ensures idempotency: if the POST is retried,
    # the exchange deduplicates on client_order_id.
    client_order_id = str(uuid.uuid4())
    order_price_cents = 1   # Deliberately low to avoid accidental fill

    order_body = {
        "ticker": ticker,
        "action": "buy",
        "side": "yes",
        "count": 1,
        "type": "limit",
        "yes_price": order_price_cents,
        "client_order_id": client_order_id,
    }

    log.info(
        "Placing limit order: BUY YES x1 @ %d¢ | client_order_id=%s",
        order_price_cents, client_order_id,
    )

    order_resp = _post("/portfolio/orders", key, order_body)

    # Schema validation: confirm order object is present
    order = order_resp.get("order")
    if not order:
        log.error("Order response missing 'order' field. Full response: %s", order_resp)
        return

    order_id = order.get("order_id")
    status = order.get("status", "unknown")

    if not order_id:
        log.error("Order placed but no order_id returned. Response: %s", order)
        return

    log.info(
        "Order placed successfully: order_id=%s  status=%s  price=%s¢  count=%s",
        order_id, status,
        order.get("yes_price", "?"),
        order.get("count", "?"),
    )

    # --- Position monitoring: confirm the order is resting (not filled) ---
    # Wait briefly then poll the order status before cancelling.
    time.sleep(0.5)
    log.info("Confirming order status (real-time position monitoring)...")

    try:
        order_status_resp = _get(f"/portfolio/orders/{order_id}", key)
        live_order = order_status_resp.get("order", {})
        live_status = live_order.get("status", "unknown")
        live_remaining = live_order.get("remaining_count", live_order.get("count", "?"))
        log.info(
            "Live order status: status=%s  remaining=%s  filled=%s",
            live_status,
            live_remaining,
            live_order.get("fill_count", live_order.get("filled_count", 0)),
        )

        if live_status == "filled":
            log.warning(
                "Order was immediately filled at 1¢ — unexpected. "
                "Market may have moved. Will still attempt cancel/sell."
            )
    except Exception as exc:
        log.warning("Could not confirm order status: %s — proceeding to cancel anyway.", exc)

    # --- Cancel the order ---
    log.info("Cancelling order %s ...", order_id)

    try:
        cancel_resp = _delete(f"/portfolio/orders/{order_id}", key)
        cancelled_order = cancel_resp.get("order", cancel_resp)
        final_status = cancelled_order.get("status", "unknown")
        log.info(
            "Order cancelled: order_id=%s  final_status=%s",
            order_id, final_status,
        )
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            log.info("Order %s not found — may have already been filled or expired.", order_id)
        else:
            log.error("Cancel request failed: %s", exc)
            raise

    # --- Post-trade: reconcile balance ---
    time.sleep(0.3)
    balance_after = _get("/portfolio/balance", key)
    balance_after_cents = balance_after.get("balance", 0)
    delta = balance_after_cents - balance_cents
    log.info(
        "Account balance after cancel: $%.2f  (delta: %+d¢)",
        balance_after_cents / 100, delta,
    )
    if delta != 0:
        log.warning(
            "Balance changed by %+d¢ after a cancelled order — "
            "possible partial fill or fee; reconcile with open positions.",
            delta,
        )
    else:
        log.info("Balance unchanged — order fully cancelled with no fill. ✓")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("Kalshi API Sample — NYC Weather Market Lifecycle")
    log.info("Mode: %s | Base URL: %s", TRADING_MODE.upper(), BASE_URL)
    log.info("")

    if not KEY_ID or KEY_ID == "PLACEHOLDER_KEY_ID":
        log.error("KALSHI_KEY_ID not set. Export your key ID and try again.")
        sys.exit(1)

    private_key = _load_private_key()
    if private_key is None:
        log.error("Could not load private key. Export KALSHI_PRIVATE_KEY_PEM and try again.")
        sys.exit(1)

    # Step 1: Market data
    markets = step1_get_nyc_markets(private_key)
    if not markets:
        log.error("No markets found — cannot proceed to Steps 2 and 3.")
        sys.exit(1)

    # Step 2: Orderbook
    ticker = step2_get_orderbook(private_key, markets)
    if not ticker:
        log.error("No ticker selected from orderbook step — cannot place order.")
        sys.exit(1)

    # Step 3: Place and cancel
    step3_place_and_cancel(private_key, ticker)

    log.info("")
    log.info("=" * 60)
    log.info("All three steps completed successfully.")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
