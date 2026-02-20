"""
NBM (National Blend of Models) probabilistic bulletin fetcher and parser.
Also includes NWS point forecast as a sanity check.

The NBP bulletin is a ~33MB plain-text file updated at 01Z, 07Z, 13Z, 19Z.
We download it once per cycle and parse all 5 city stations from the same string.

Run standalone to verify parsing:
  python -m data.weather
"""

import re
import logging
import datetime
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import requests

from config import (
    CityConfig,
    CITIES,
    NBM_BASE_URL,
    NBM_CYCLES,
    NBM_CYCLE_LAG_HOURS,
    NWS_POINTS_URL,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 90  # seconds for the large NBM download


@dataclass
class NBMForecast:
    station: str
    valid_date: str           # "YYYY-MM-DD" — the day we're forecasting
    run_cycle: str            # e.g. "19"
    mu: float                 # Calibrated mean (set to p50 before calibration)
    sigma: float              # Estimated std dev
    p10: float
    p25: float
    p50: float                # Median — used as raw mu
    p75: float
    p90: float
    fetched_at: str           # ISO8601


# ---------------------------------------------------------------------------
# Cycle selection
# ---------------------------------------------------------------------------

def get_latest_available_cycle() -> Tuple[str, str]:
    """
    Returns (date_str, cycle) for the most recent NBM cycle that should be
    available on NOMADS (current UTC time minus LAG_HOURS).

    Returns: ("YYYYMMDD", "19") style tuple
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    effective = now_utc - datetime.timedelta(hours=NBM_CYCLE_LAG_HOURS)

    for cycle in NBM_CYCLES:  # ["19", "13", "07", "01"]
        cycle_hour = int(cycle)
        if effective.hour >= cycle_hour:
            date_str = effective.strftime("%Y%m%d")
            return date_str, cycle

    # Fell through — use previous day's 19Z
    prev = effective - datetime.timedelta(days=1)
    return prev.strftime("%Y%m%d"), "19"


def build_nbm_url(date_str: str, cycle: str) -> str:
    return f"{NBM_BASE_URL}/blend.{date_str}/{cycle}/text/blend_nbptx.t{cycle}z"


# ---------------------------------------------------------------------------
# Bulletin download (shared across all cities)
# ---------------------------------------------------------------------------

_bulletin_cache: Dict[str, str] = {}  # key: "date_str#cycle" → text


def fetch_nbm_bulletin(date_str: str, cycle: str) -> str:
    """
    Downloads the NBP bulletin from NOMADS. Caches within the process
    so the 33MB file is only fetched once per scheduling cycle.
    """
    cache_key = f"{date_str}#{cycle}"
    if cache_key in _bulletin_cache:
        logger.debug("Using cached NBM bulletin for %s", cache_key)
        return _bulletin_cache[cache_key]

    url = build_nbm_url(date_str, cycle)
    logger.info("Downloading NBM bulletin from %s", url)
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, stream=True)
    resp.raise_for_status()

    text = resp.text
    logger.info("NBM bulletin downloaded: %.1f MB", len(text) / 1_048_576)
    _bulletin_cache[cache_key] = text
    return text


def clear_bulletin_cache() -> None:
    _bulletin_cache.clear()


# ---------------------------------------------------------------------------
# Station block extraction
# ---------------------------------------------------------------------------

def extract_station_block(bulletin_text: str, station: str) -> Optional[str]:
    """
    Extracts the text block for one station from the full NBP bulletin.
    Each block starts with a line like: "KLAX   NBP  19 Feb 2026  19Z"
    and ends at the next station block or end-of-file.
    """
    # Match the station header line
    pattern = rf"^{re.escape(station)}\s+NBP\s+"
    matches = list(re.finditer(pattern, bulletin_text, re.MULTILINE))
    if not matches:
        logger.warning("Station %s not found in bulletin", station)
        return None

    start = matches[0].start()
    # Find the next station block start (any all-caps 4-letter station code)
    next_block = re.search(r"^[A-Z]{4}\s+NBP\s+", bulletin_text[start + 1:], re.MULTILINE)
    if next_block:
        end = start + 1 + next_block.start()
        return bulletin_text[start:end]
    return bulletin_text[start:]


# ---------------------------------------------------------------------------
# NBP station block parser
# ---------------------------------------------------------------------------

def _parse_row(block: str, row_label: str) -> Optional[list]:
    """
    Extract the values from a named row in the station block.
    Row format: "TXNP5    62    48    65    50   ..."
    Returns list of ints or None if the row is not found.
    """
    pattern = rf"^\s*{re.escape(row_label)}\s+([\d\s-]+)$"
    m = re.search(pattern, block, re.MULTILINE)
    if not m:
        return None
    return [int(x) for x in m.group(1).split()]


def _find_tomorrow_max_column(block: str, valid_date: datetime.date) -> Optional[int]:
    """
    Finds the column index corresponding to tomorrow's MaxT (00Z period).
    The DT/HR row in the bulletin marks valid UTC times.

    NBP columns alternate: ..., 00Z today, 12Z today, 00Z tomorrow, 12Z tomorrow, ...
    MaxT is at 00Z of the target date (i.e. end of the day following).

    Strategy: find the header row that contains date info and match tomorrow 00Z.
    The simpler approach: MaxT columns are at even indices (0, 2, 4...) in the
    TXNMN row, and the first 00Z column after the current time is tomorrow's max.

    For bulletins run at 19Z on date D, valid periods are typically:
      col 0: 00Z D+1 (tomorrow's MaxT) — this is what we want
      col 1: 12Z D+1 (tonight's MinT)
      col 2: 00Z D+2
      ...

    This returns column index 0 (the first 00Z period) as tomorrow's MaxT.
    We validate with the DT row if available.
    """
    # Look for a DT row with date info
    dt_match = re.search(r"DT\s+(.*)", block)
    if not dt_match:
        # Default: column 0 is tomorrow's MaxT for 19Z runs
        return 0

    dt_line = dt_match.group(1)
    tomorrow_str = valid_date.strftime("%-d/%m").lstrip("0")  # e.g. "20/02" on Windows needs adjustment
    # Windows-compatible date formatting
    tomorrow_day = str(valid_date.day)
    tomorrow_month = str(valid_date.month)

    # Try to find tomorrow's date in the DT header
    # DT row often looks like: "/20     /20     /21     /21"
    # where /20 means Feb 20 (day only)
    day_pattern = rf"/{tomorrow_day}\b"
    cols = re.findall(r"/(\d+)", dt_line)
    if cols:
        try:
            idx = cols.index(tomorrow_day)
            return idx
        except ValueError:
            pass

    # Fallback: return 0 (valid for 19Z cycle where first column = tomorrow MaxT)
    return 0


def parse_nbp_station_block(
    block: str,
    station: str,
    run_date: datetime.date,
    cycle: str,
) -> Optional[NBMForecast]:
    """
    Parses temperature percentile rows from an NBP station block.

    TXNP1 = 10th pct, TXNP2 = 25th, TXNP5 = 50th, TXNP7 = 75th, TXNP9 = 90th
    TXNMN = deterministic mean (used as fallback)

    Returns NBMForecast with p10..p90, mu (=p50), sigma estimated from quantiles.
    """
    # Tomorrow's date depends on the cycle — for 19Z on day D, tomorrow = D+1
    # For 01Z on day D, the first columns may already represent today's highs
    # Conservative: always take the first MaxT column
    valid_date = run_date + datetime.timedelta(days=1)
    col_idx = _find_tomorrow_max_column(block, valid_date)

    def get_col(label: str) -> Optional[float]:
        row = _parse_row(block, label)
        if row is None or col_idx >= len(row):
            return None
        return float(row[col_idx])

    p10 = get_col("TXNP1")
    p25 = get_col("TXNP2")
    p50 = get_col("TXNP5")
    p75 = get_col("TXNP7")
    p90 = get_col("TXNP9")

    if p50 is None:
        # Try deterministic mean as fallback
        p50 = get_col("TXNMN")

    if p50 is None:
        logger.warning("Could not parse MaxT for station %s", station)
        return None

    # Fill missing percentiles by symmetry around median
    if p10 is None and p90 is not None:
        p10 = 2 * p50 - p90
    if p90 is None and p10 is not None:
        p90 = 2 * p50 - p10
    if p25 is None:
        p25 = p50 - (p50 - p10) * 0.5 if p10 else p50
    if p75 is None:
        p75 = p50 + (p90 - p50) * 0.5 if p90 else p50

    # Sigma estimation: average of IQR and 80th-pct-range methods
    sigma_estimates = []
    if p25 is not None and p75 is not None:
        sigma_estimates.append((p75 - p25) / (2 * 0.6745))
    if p10 is not None and p90 is not None:
        sigma_estimates.append((p90 - p10) / (2 * 1.282))

    sigma = sum(sigma_estimates) / len(sigma_estimates) if sigma_estimates else 4.0
    sigma = max(sigma, 1.0)  # floor at 1°F

    return NBMForecast(
        station=station,
        valid_date=valid_date.isoformat(),
        run_cycle=cycle,
        mu=p50,      # Raw NBM median — calibration applied later
        sigma=sigma,
        p10=p10 or (p50 - 1.28 * sigma),
        p25=p25 or (p50 - 0.67 * sigma),
        p50=p50,
        p75=p75 or (p50 + 0.67 * sigma),
        p90=p90 or (p50 + 1.28 * sigma),
        fetched_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Multi-city fetch (single bulletin download)
# ---------------------------------------------------------------------------

def fetch_all_city_forecasts(
    cities: Optional[Dict[str, CityConfig]] = None,
) -> Dict[str, NBMForecast]:
    """
    Downloads one NBM bulletin and parses forecasts for all 5 cities.
    Returns dict keyed by city code ("LA", "NYC", etc.).
    """
    if cities is None:
        cities = CITIES

    date_str, cycle = get_latest_available_cycle()
    run_date = datetime.datetime.strptime(date_str, "%Y%m%d").date()

    try:
        bulletin = fetch_nbm_bulletin(date_str, cycle)
    except requests.HTTPError as e:
        logger.error("Failed to download NBM bulletin: %s", e)
        # Try previous cycle as fallback
        cycle_idx = NBM_CYCLES.index(cycle) + 1
        if cycle_idx < len(NBM_CYCLES):
            fallback_cycle = NBM_CYCLES[cycle_idx]
            logger.info("Trying fallback cycle %s", fallback_cycle)
            bulletin = fetch_nbm_bulletin(date_str, fallback_cycle)
            cycle = fallback_cycle
        else:
            raise

    results = {}
    for city_code, city_cfg in cities.items():
        block = extract_station_block(bulletin, city_cfg.nbm_station)
        if block is None:
            logger.error("No block found for %s (%s)", city_code, city_cfg.nbm_station)
            continue
        forecast = parse_nbp_station_block(block, city_cfg.nbm_station, run_date, cycle)
        if forecast is not None:
            results[city_code] = forecast
            logger.info(
                "NBM %s (%s): mu=%.1f°F sigma=%.1f°F | p10=%.0f p50=%.0f p90=%.0f",
                city_code,
                city_cfg.nbm_station,
                forecast.mu,
                forecast.sigma,
                forecast.p10,
                forecast.p50,
                forecast.p90,
            )

    return results


# ---------------------------------------------------------------------------
# NWS point forecast (sanity check)
# ---------------------------------------------------------------------------

def get_nws_forecast_high(city: CityConfig) -> Optional[float]:
    """
    Fetches tomorrow's high from NWS API as a sanity check against NBM.
    Returns temperature in °F or None on failure.
    """
    try:
        points_url = NWS_POINTS_URL.format(lat=city.lat, lon=city.lon)
        headers = {"User-Agent": "kalshi-edge-trader (educational trading bot)"}
        resp = requests.get(points_url, headers=headers, timeout=15)
        resp.raise_for_status()
        forecast_url = resp.json()["properties"]["forecast"]

        resp2 = requests.get(forecast_url, headers=headers, timeout=15)
        resp2.raise_for_status()
        periods = resp2.json()["properties"]["periods"]

        # Find tomorrow's daytime period
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        for period in periods:
            start = period.get("startTime", "")
            if tomorrow in start and period.get("isDaytime", False):
                temp = period.get("temperature")
                unit = period.get("temperatureUnit", "F")
                if unit == "C":
                    temp = temp * 9 / 5 + 32
                logger.info("NWS sanity check %s: %s°F", city.name, temp)
                return float(temp)
    except Exception as e:
        logger.warning("NWS forecast failed for %s: %s", city.name, e)
    return None


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("Fetching NBM forecasts for all cities...")
    forecasts = fetch_all_city_forecasts()

    print("\n" + "=" * 60)
    print(f"{'City':<6} {'Date':<12} {'Cycle':<6} {'Mu':>6} {'Sigma':>6} {'P10':>5} {'P50':>5} {'P90':>5}")
    print("=" * 60)
    for city_code, f in forecasts.items():
        print(
            f"{city_code:<6} {f.valid_date:<12} {f.run_cycle+'Z':<6} "
            f"{f.mu:>6.1f} {f.sigma:>6.1f} {f.p10:>5.0f} {f.p50:>5.0f} {f.p90:>5.0f}"
        )

    print("\nNWS sanity checks:")
    for city_code, city_cfg in CITIES.items():
        nws_high = get_nws_forecast_high(city_cfg)
        nbm_mu = forecasts.get(city_code, {})
        nbm_val = forecasts[city_code].mu if city_code in forecasts else "N/A"
        print(f"  {city_code}: NWS={nws_high}°F  NBM_mu={nbm_val}°F")
