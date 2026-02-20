"""
Kalshi API client with RSA-PSS authentication.

Handles:
- Market discovery for tomorrow's temperature events
- Orderbook fetching for edge calculation
- Order placement (paper / demo / live modes)
- Balance queries

Auth reference: https://docs.kalshi.com/getting_started/api_keys
"""

import re
import time
import uuid
import logging
import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from config import (
    DEMO_BASE_URL,
    PROD_BASE_URL,
    KALSHI_KEY_ID,
    KALSHI_PRIVATE_KEY_PEM,
    TRADING_MODE,
    STARTING_BALANCE,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20  # seconds
MIN_REQUEST_INTERVAL = 0.35  # throttle to reduce public API 429s
KALSHI_MARKET_TZ = ZoneInfo("America/New_York")


@dataclass
class KalshiMarket:
    ticker: str
    event_ticker: str
    yes_ask: float             # Best YES ask price (0.00–1.00)
    yes_bid: float             # Best YES bid price (0.00–1.00)
    yes_sub_title: str         # e.g. "57° to 58°"
    temp_low: Optional[float]  # Lower bound in °F (None for open-low)
    temp_high: Optional[float] # Upper bound in °F (None for open-high)
    is_open_low: bool          # True if "X° or lower"
    is_open_high: bool         # True if "X° or higher"
    status: str                # "open", "closed", etc.
    volume: int = 0


@dataclass
class KalshiOrderbook:
    ticker: str
    yes_bids: List[Dict]   # [{"price": 0.45, "quantity": 10}, ...]
    yes_asks: List[Dict]   # [{"price": 0.50, "quantity": 5}, ...]

    def best_ask(self) -> Optional[float]:
        if not self.yes_asks:
            return None
        return min(entry["price"] for entry in self.yes_asks)

    def best_bid(self) -> Optional[float]:
        if not self.yes_bids:
            return None
        return max(entry["price"] for entry in self.yes_bids)

    def spread(self) -> Optional[float]:
        ask = self.best_ask()
        bid = self.best_bid()
        if ask is not None and bid is not None:
            return ask - bid
        return None


class KalshiClient:
    def __init__(self):
        self.base_url = DEMO_BASE_URL if TRADING_MODE == "demo" else PROD_BASE_URL
        self.key_id = KALSHI_KEY_ID
        self._private_key = self._load_private_key()
        self._last_request_time = 0.0

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _load_private_key(self):
        pem = KALSHI_PRIVATE_KEY_PEM
        if pem == "PLACEHOLDER_PEM" or not pem:
            logger.warning("No Kalshi private key configured — running in read-only mode")
            return None
        # Handle escaped newlines from environment variables
        pem = pem.replace("\\n", "\n")
        if not pem.strip().startswith("-----"):
            logger.warning("Invalid PEM format for private key")
            return None
        try:
            return serialization.load_pem_private_key(pem.encode(), password=None)
        except Exception as e:
            logger.error("Failed to load private key: %s", e)
            return None

    def _sign_request(self, method: str, path: str) -> dict:
        """Generate KALSHI auth headers using RSA-PSS SHA-256."""
        timestamp_ms = str(int(time.time() * 1000))
        # Path without query string
        path_no_query = path.split("?")[0]
        message = f"{timestamp_ms}{method.upper()}{path_no_query}".encode()

        if self._private_key is None:
            return {}

        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        import base64
        sig_b64 = base64.b64encode(signature).decode()
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": sig_b64,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self.base_url + path
        headers = {"Content-Type": "application/json"}
        headers.update(self._sign_request("GET", path))

        for attempt in range(3):
            self._rate_limit()
            resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp.json()

            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else (0.5 * (2 ** attempt))
            logger.warning("Kalshi rate limit hit on %s; retrying in %.1fs", path, delay)
            time.sleep(delay)

        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        self._rate_limit()
        url = self.base_url + path
        headers = {"Content-Type": "application/json"}
        headers.update(self._sign_request("POST", path))
        resp = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Market discovery
    # ------------------------------------------------------------------

    def get_events_for_series(self, series_ticker: str) -> List[dict]:
        """GET /events?series_ticker={series}&status=open."""
        try:
            data = self._get("/events", params={"series_ticker": series_ticker, "status": "open"})
            return data.get("events", [])
        except Exception as e:
            logger.error("Failed to get events for series %s: %s", series_ticker, e)
            return []

    def _format_event_ticker_for_date(self, series_ticker: str, date_value: datetime.date) -> str:
        """Construct canonical Kalshi event ticker suffix YYMONDD, e.g. KXHIGHNY-26FEB20."""
        return f"{series_ticker}-{date_value.strftime('%y%b%d').upper()}"

    def get_tomorrow_event_ticker(self, series_ticker: str) -> Optional[str]:
        """
        Finds the event ticker for tomorrow's date in a given series.
        Uses Kalshi market timezone (America/New_York) so deployments running in UTC
        still target the correct "tomorrow" market from a US trading perspective.

        Kalshi close_time for a daily temperature market is ~midnight UTC at the end
        of the *measurement* day — e.g. the Feb 20 event closes at 2026-02-21T04:59Z
        (= 11:59pm ET Feb 20).  So event_date == close_time_ET.date() - 1 day.
        We want tomorrow's event, meaning close_time_ET.date() == tomorrow + 1 day.
        """
        now_market_tz = datetime.datetime.now(tz=KALSHI_MARKET_TZ)
        tomorrow = now_market_tz.date() + datetime.timedelta(days=1)
        # The close_time ET date for a "tomorrow" event is tomorrow + 1
        expected_close_date = tomorrow + datetime.timedelta(days=1)

        events = self.get_events_for_series(series_ticker)
        for event in events:
            close_time = event.get("close_time", "")
            if not close_time:
                continue
            try:
                close_dt = datetime.datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                if close_dt.tzinfo is None:
                    close_dt = close_dt.replace(tzinfo=datetime.timezone.utc)
                close_date_et = close_dt.astimezone(KALSHI_MARKET_TZ).date()
                if close_date_et == expected_close_date:
                    return event["event_ticker"]
            except (ValueError, KeyError):
                continue

        fallback_ticker = self._format_event_ticker_for_date(series_ticker, tomorrow)
        logger.warning(
            "No tomorrow event found for series %s via /events; falling back to inferred ticker %s",
            series_ticker,
            fallback_ticker,
        )
        return fallback_ticker

    def get_markets_for_event(self, event_ticker: str) -> List[KalshiMarket]:
        """GET /markets?event_ticker={ticker}&status=open."""
        markets: List[dict] = []
        try:
            data = self._get("/markets", params={"event_ticker": event_ticker, "status": "open"})
            markets = data.get("markets", [])
        except Exception as e:
            logger.error("Failed to get markets for event %s: %s", event_ticker, e)
            return []

        result = []
        for m in markets:
            try:
                yes_ask = self._parse_price(m.get("yes_ask") or m.get("yes_ask_price") or 0)
                yes_bid = self._parse_price(m.get("yes_bid") or m.get("yes_bid_price") or 0)
                subtitle = m.get("yes_sub_title") or m.get("subtitle") or ""
                temp_low, temp_high, is_open_low, is_open_high = self._parse_bounds_from_market(m)

                market_status = (m.get("status", "").lower() or "open")
                if market_status not in {"open", "active"}:
                    continue

                result.append(KalshiMarket(
                    ticker=m["ticker"],
                    event_ticker=event_ticker,
                    yes_ask=yes_ask,
                    yes_bid=yes_bid,
                    yes_sub_title=subtitle,
                    temp_low=temp_low,
                    temp_high=temp_high,
                    is_open_low=is_open_low,
                    is_open_high=is_open_high,
                    status=market_status,
                    volume=int(m.get("volume", 0)),
                ))
            except Exception as e:
                logger.debug("Skipping market %s: %s", m.get("ticker", "?"), e)
                continue

        return result

    def get_markets_for_series_tomorrow(self, series_ticker: str) -> List[KalshiMarket]:
        """GET /markets?series_ticker=...&status=open, filtered to tomorrow's event.

        Kalshi close_time for a daily temperature market is ~midnight UTC at the end
        of the measurement day — e.g. the Feb 20 event closes at 2026-02-21T04:59Z
        (= 11:59pm ET Feb 20).  So event_date == close_time_ET.date() - 1 day.
        We want tomorrow's event, so we keep markets where close_time_ET.date()
        == tomorrow + 1 day.
        """
        now_market_tz = datetime.datetime.now(tz=KALSHI_MARKET_TZ)
        tomorrow = now_market_tz.date() + datetime.timedelta(days=1)
        # A "tomorrow" event closes at midnight ET on the day after tomorrow
        expected_close_date = tomorrow + datetime.timedelta(days=1)

        markets: List[dict] = []
        try:
            data = self._get("/markets", params={"series_ticker": series_ticker, "status": "open"})
            markets = data.get("markets", [])
        except Exception as e:
            logger.error("Failed to get markets for series %s: %s", series_ticker, e)
            return []

        filtered = []
        for m in markets:
            close_time = m.get("close_time", "")
            if not close_time:
                continue
            try:
                close_dt = datetime.datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                if close_dt.tzinfo is None:
                    close_dt = close_dt.replace(tzinfo=datetime.timezone.utc)
                if close_dt.astimezone(KALSHI_MARKET_TZ).date() != expected_close_date:
                    continue
            except ValueError:
                continue

            market_status = (m.get("status", "").lower() or "open")
            if market_status not in {"open", "active"}:
                continue
            filtered.append(m)

        if not filtered:
            return []

        result = []
        for m in filtered:
            try:
                yes_ask = self._parse_price(m.get("yes_ask") or m.get("yes_ask_price") or 0)
                yes_bid = self._parse_price(m.get("yes_bid") or m.get("yes_bid_price") or 0)
                subtitle = m.get("yes_sub_title") or m.get("subtitle") or ""
                temp_low, temp_high, is_open_low, is_open_high = self._parse_bounds_from_market(m)

                result.append(KalshiMarket(
                    ticker=m["ticker"],
                    event_ticker=str(m.get("event_ticker", "")),
                    yes_ask=yes_ask,
                    yes_bid=yes_bid,
                    yes_sub_title=subtitle,
                    temp_low=temp_low,
                    temp_high=temp_high,
                    is_open_low=is_open_low,
                    is_open_high=is_open_high,
                    status=(m.get("status", "").lower() or "open"),
                    volume=int(m.get("volume", 0)),
                ))
            except Exception as e:
                logger.debug("Skipping market %s: %s", m.get("ticker", "?"), e)
                continue

        return result

    def _parse_price(self, raw) -> float:
        """Convert Kalshi price to float 0.0-1.0. Prices may be cents (int) or decimal strings."""
        if raw is None:
            return 0.0
        if isinstance(raw, str):
            raw = float(raw)
        # Kalshi prices: if > 1, assume cents (0-100), else already 0-1
        if isinstance(raw, (int, float)) and raw > 1:
            return raw / 100.0
        return float(raw)

    def _parse_temp_range(self, subtitle: str) -> Tuple[Optional[float], Optional[float], bool, bool]:
        """
        Parse temperature range from a Kalshi market yes_sub_title string.

        Confirmed real Kalshi subtitle formats (as of Feb 2026):
          "62° to 63°"    → bounded bin  (57.0, 58.0, False, False)
          "55° or below"  → open-low cap (None, 55.0, True,  False)
          "64° or above"  → open-high    (64.0, None, False, True)

        Also handles legacy / variant forms seen historically:
          "X° or lower" / "X° or higher"
          "Below X°"    / "Above X°"
          "X°" alone    → treated as point estimate ±0.5°F

        Degree sign is matched liberally (Unicode °, ASCII, or absent).
        """
        # Normalise: strip whitespace, collapse unicode degree variants to plain "°"
        s = subtitle.strip().replace("\u00b0", "°").replace("\u02da", "°")

        # Degree pattern fragment (degree sign optional, may have spaces)
        DEG = r"[°]?\s*"
        NUM = r"(\d+(?:\.\d+)?)"

        # "X° to Y°"  or  "X° - Y°"
        m = re.match(rf"{NUM}{DEG}(?:to|-)\s*{NUM}{DEG}$", s, re.IGNORECASE)
        if m:
            return float(m.group(1)), float(m.group(2)), False, False

        # "X° or below" / "X° or lower"
        m = re.match(rf"{NUM}{DEG}or\s+(?:below|lower)\s*$", s, re.IGNORECASE)
        if m:
            return None, float(m.group(1)), True, False

        # "X° or above" / "X° or higher"
        m = re.match(rf"{NUM}{DEG}or\s+(?:above|higher)\s*$", s, re.IGNORECASE)
        if m:
            return float(m.group(1)), None, False, True

        # "Below X°" / "Under X°"
        m = re.match(rf"(?:below|under)\s+{NUM}{DEG}$", s, re.IGNORECASE)
        if m:
            return None, float(m.group(1)), True, False

        # "Above X°" / "Over X°"
        m = re.match(rf"(?:above|over)\s+{NUM}{DEG}$", s, re.IGNORECASE)
        if m:
            return float(m.group(1)), None, False, True

        # "X°" alone — treat as a ±0.5°F point estimate
        m = re.match(rf"{NUM}{DEG}$", s)
        if m:
            val = float(m.group(1))
            return val - 0.5, val + 0.5, False, False

        return None, None, False, False

    def _parse_bounds_from_market(
        self, raw: dict
    ) -> Tuple[Optional[float], Optional[float], bool, bool]:
        """
        Derive temperature bounds from a raw Kalshi market dict.

        Prefers the authoritative `floor_strike` + `strike_type` fields
        (present in the API response) over subtitle text parsing.

        Kalshi `strike_type` values observed:
          "greater"   → YES if temp >  floor_strike  (open-high from floor_strike+1)
          "less"      → YES if temp <  floor_strike  (open-low  up to floor_strike-1)
          "between"   → YES if floor_strike <= temp <= ceil_strike (bounded)

        Falls back to subtitle parsing when strike fields are absent.
        """
        strike = raw.get("floor_strike")
        strike_type = (raw.get("strike_type") or "").lower()

        if strike is not None and strike_type:
            s = float(strike)
            if strike_type == "greater":
                # YES resolves if temp > strike, i.e. temp >= strike + 1
                return s + 1.0, None, False, True
            if strike_type == "less":
                # YES resolves if temp < strike, i.e. temp <= strike - 1
                return None, s - 1.0, True, False
            if strike_type == "between":
                ceil_strike = raw.get("ceil_strike") or raw.get("floor_strike")
                return s, float(ceil_strike), False, False

        # Fallback: parse yes_sub_title / subtitle text
        subtitle = raw.get("yes_sub_title") or raw.get("subtitle") or ""
        return self._parse_temp_range(subtitle)

    # ------------------------------------------------------------------
    # Orderbook
    # ------------------------------------------------------------------

    def get_orderbook(self, ticker: str, depth: int = 10) -> Optional[KalshiOrderbook]:
        """GET /markets/{ticker}/orderbook"""
        try:
            data = self._get(f"/markets/{ticker}/orderbook", params={"depth": depth})
            ob = data.get("orderbook", data)

            # Kalshi YES bids = price people pay for YES
            # Kalshi NO bids = what they pay for NO (= 100 - YES ask equivalent)
            yes_bids = [
                {"price": self._parse_price(entry[0]), "quantity": entry[1]}
                for entry in (ob.get("yes", []) or [])
            ]
            # NO bids imply YES asks: NO_bid_price = 1 - YES_ask_price
            no_bids = ob.get("no", []) or []
            yes_asks = [
                {"price": 1.0 - self._parse_price(entry[0]), "quantity": entry[1]}
                for entry in no_bids
            ]

            return KalshiOrderbook(ticker=ticker, yes_bids=yes_bids, yes_asks=yes_asks)
        except Exception as e:
            logger.error("Failed to get orderbook for %s: %s", ticker, e)
            return None

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------

    def get_balance(self) -> float:
        """GET /portfolio/balance — returns balance in dollars."""
        if self._private_key is None or TRADING_MODE == "paper":
            return STARTING_BALANCE
        try:
            data = self._get("/portfolio/balance")
            # Balance returned in cents as integer
            balance_cents = data.get("balance", 0)
            return balance_cents / 100.0
        except Exception as e:
            logger.error("Failed to get balance: %s", e)
            return STARTING_BALANCE

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def place_order(
        self,
        ticker: str,
        side: str,              # "yes" or "no"
        action: str,            # "buy" or "sell"
        count: int,             # Number of contracts
        yes_price_cents: int,   # Price 1-99
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        """
        POST /portfolio/orders
        In paper mode: returns a mock response without hitting the API.
        """
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())

        order_body = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": count,
            "type": "limit",
            "yes_price": yes_price_cents,
            "client_order_id": client_order_id,
        }

        if TRADING_MODE == "paper":
            logger.info(
                "[PAPER] Would place order: %s %s %s x%d @ %d¢ (ticker=%s)",
                action, side, ticker, count, yes_price_cents, ticker,
            )
            return {
                "order": {
                    "order_id": f"paper-{client_order_id[:8]}",
                    "client_order_id": client_order_id,
                    "ticker": ticker,
                    "status": "resting",
                    "side": side,
                    "action": action,
                    "count": count,
                    "yes_price": yes_price_cents,
                    "mode": "paper",
                }
            }

        if self._private_key is None:
            logger.error("Cannot place order: no private key configured")
            return None

        try:
            result = self._post("/portfolio/orders", order_body)
            logger.info(
                "Order placed: %s | %s x%d @ %d¢ | id=%s",
                ticker,
                f"{action} {side}",
                count,
                yes_price_cents,
                result.get("order", {}).get("order_id", "?"),
            )
            return result
        except requests.HTTPError as e:
            logger.error("Order placement failed for %s: %s | body=%s", ticker, e, order_body)
            return None

    # ------------------------------------------------------------------
    # Order management (cancel, query)
    # ------------------------------------------------------------------

    def _delete(self, path: str) -> dict:
        """Authenticated DELETE request."""
        self._rate_limit()
        url = self.base_url + path
        headers = {"Content-Type": "application/json"}
        headers.update(self._sign_request("DELETE", path))
        resp = requests.delete(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def cancel_order(self, order_id: str) -> dict:
        """
        DELETE /portfolio/orders/{order_id}
        Cancels a resting open order on Kalshi.
        In paper mode: returns a mock cancellation response.
        """
        if TRADING_MODE == "paper":
            logger.info("[PAPER] Would cancel order: %s", order_id)
            return {"order": {"order_id": order_id, "status": "canceled"}}

        if self._private_key is None:
            logger.error("Cannot cancel order: no private key configured")
            return {}

        try:
            result = self._delete(f"/portfolio/orders/{order_id}")
            logger.info("Canceled order: %s", order_id)
            return result
        except requests.HTTPError as e:
            logger.error("Failed to cancel order %s: %s", order_id, e)
            return {}

    def get_open_orders(self) -> List[dict]:
        """
        GET /portfolio/orders?status=resting
        Returns list of open (resting) orders from the Kalshi portfolio.
        In paper mode: returns empty list (no real orders exist).
        """
        if TRADING_MODE == "paper" or self._private_key is None:
            return []
        try:
            data = self._get("/portfolio/orders", params={"status": "resting"})
            return data.get("orders", [])
        except Exception as e:
            logger.error("Failed to fetch open orders: %s", e)
            return []

    def get_positions(self) -> List[dict]:
        """
        GET /portfolio/positions
        Returns all current open positions with quantity, cost, and P&L.
        In paper mode: returns empty list.
        """
        if TRADING_MODE == "paper" or self._private_key is None:
            return []
        try:
            data = self._get("/portfolio/positions")
            return data.get("market_positions", [])
        except Exception as e:
            logger.error("Failed to fetch positions: %s", e)
            return []

    def get_order_status(self, order_id: str) -> Optional[dict]:
        """
        GET /portfolio/orders/{order_id}
        Returns current status of a specific order.
        """
        if self._private_key is None:
            return None
        try:
            data = self._get(f"/portfolio/orders/{order_id}")
            return data.get("order", data)
        except Exception as e:
            logger.error("Failed to fetch order status %s: %s", order_id, e)
            return None

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def get_city_markets(self, series_ticker: str) -> List[KalshiMarket]:
        """
        End-to-end market discovery for tomorrow in ET.
        Prefer direct series->markets lookup (fewer API calls), then fallback to event lookup.
        """
        markets = self.get_markets_for_series_tomorrow(series_ticker)
        if markets:
            return markets

        event_ticker = self.get_tomorrow_event_ticker(series_ticker)
        if event_ticker is None:
            return []
        return self.get_markets_for_event(event_ticker)


# ---------------------------------------------------------------------------
# Standalone test (no auth needed for public market data)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    from config import CITIES

    client = KalshiClient()

    for city_code, city_cfg in CITIES.items():
        print(f"\n{'='*60}")
        print(f"City: {city_cfg.display_name} | Series: {city_cfg.kalshi_series}")
        markets = client.get_city_markets(city_cfg.kalshi_series)
        if not markets:
            print("  No open markets found for tomorrow.")
            continue
        print(f"  Found {len(markets)} markets for tomorrow")
        for mkt in markets[:5]:
            print(
                f"  {mkt.ticker:<40} {mkt.yes_sub_title:<20} "
                f"ask={mkt.yes_ask:.2f} bid={mkt.yes_bid:.2f}"
            )
