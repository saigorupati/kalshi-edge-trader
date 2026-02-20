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

    # Normalize line endings — the NOMADS server returns CRLF (\r\n) which
    # breaks re.MULTILINE's $ anchor since \r is not whitespace in the regex.
    text = resp.text.replace("\r\n", "\n").replace("\r", "\n")
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

    NBM V4.3 bulletin header format (note leading space):
      " KLAX    NBM V4.3 NBP GUIDANCE    2/20/2026  0100 UTC"

    We match:  optional-whitespace + STATION + whitespace + "NBM" or "NBP"
    to handle both old (NBP only) and current (NBM V4.x NBP GUIDANCE) formats.
    """
    # Match the station header line. The bulletin format is:
    #   " KLAX    NBM V4.3 NBP GUIDANCE    2/20/2026  0100 UTC"
    # The station ID appears at the START of its own line (after optional spaces
    # on that same line). We use [ \t]* (spaces/tabs only, not \n) so we don't
    # consume a blank line and accidentally match on the next line.
    pattern = rf"^[ \t]*{re.escape(station)}[ \t]+NBM"
    matches = list(re.finditer(pattern, bulletin_text, re.MULTILINE))

    # Fallback: old-style "KLAX   NBP" header (no "NBM" prefix)
    if not matches:
        pattern = rf"^[ \t]*{re.escape(station)}[ \t]+NBP"
        matches = list(re.finditer(pattern, bulletin_text, re.MULTILINE))

    if not matches:
        logger.warning("Station %s not found in bulletin", station)
        return None

    start = matches[0].start()

    # Skip past the end of the matched header line before searching for the
    # next station — otherwise the search re-matches the same header (block = 1 char).
    header_line_end = bulletin_text.find("\n", start)
    if header_line_end == -1:
        return bulletin_text[start:]
    search_from = header_line_end + 1

    # Find the next station block header (same no-newline anchor)
    next_block = re.search(
        r"^[ \t]*[A-Z]{4}[ \t]+NBM",
        bulletin_text[search_from:],
        re.MULTILINE,
    )
    if not next_block:
        # Fallback: old-style header
        next_block = re.search(
            r"^[ \t]*[A-Z]{4}[ \t]+NBP",
            bulletin_text[search_from:],
            re.MULTILINE,
        )

    if next_block:
        end = search_from + next_block.start()
        return bulletin_text[start:end]
    return bulletin_text[start:]


# ---------------------------------------------------------------------------
# NBP station block parser
# ---------------------------------------------------------------------------

def _parse_row(block: str, row_label: str) -> Optional[list]:
    """
    Extract the values from a named row in the station block.

    NBM V4.3 row format (pipe-delimited groups of two values):
      " TXNP5  55  43| 64  48| 70  51| 75  55| ..."

    The '|' characters are segment separators — we strip them before
    parsing so that only the numeric values remain.

    Returns flat list of ints in column order, or None if row not found.
    """
    pattern = rf"^\s*{re.escape(row_label)}\s+([\d\s|/-]+)$"
    m = re.search(pattern, block, re.MULTILINE)
    if not m:
        return None
    # Strip pipe characters then split on whitespace
    raw = m.group(1).replace("|", " ")
    tokens = raw.split()
    try:
        return [int(x) for x in tokens]
    except ValueError:
        return None


def _find_tomorrow_max_column(block: str, valid_date: datetime.date) -> int:
    """
    Finds the flat column index (after pipe-stripping) for tomorrow's MaxT.

    NBM V4.3 column layout — each day has TWO values per group (00Z, 12Z):
      SAT 21| SUN 22| ...
      00  12| 00  12| ...
      col0 col1 | col2 col3 | ...

    MaxT = 00Z value = even-indexed columns (0, 2, 4, ...).
    The bulletin header row looks like:
      "        SAT 21| SUN 22| MON 23|..."

    We find which day-group contains valid_date and return the corresponding
    even column index. Fall back to 0 (first column = soonest MaxT) on any error.
    """
    tomorrow_day = str(valid_date.day)   # e.g. "21"

    # Look for the date header row — contains day numbers after "SAT", "SUN" etc.
    # Format: "        SAT 21| SUN 22| MON 23|"
    date_header_match = re.search(
        r"(?:MON|TUE|WED|THU|FRI|SAT|SUN)[ \t]+\d+\|",
        block,
    )
    if not date_header_match:
        return 0  # Safest default — first column is the nearest MaxT

    date_header_line = block[
        block.rfind("\n", 0, date_header_match.start()) + 1:
        block.find("\n", date_header_match.start())
    ]

    # Extract day numbers in order: ["21", "22", "23", ...]
    day_numbers = re.findall(r"(?:MON|TUE|WED|THU|FRI|SAT|SUN)\s+(\d+)", date_header_line)
    if not day_numbers:
        return 0

    try:
        group_idx = day_numbers.index(tomorrow_day)
        # Each group = 2 flat columns (00Z and 12Z); MaxT = 00Z = even index
        return group_idx * 2
    except ValueError:
        # valid_date not in header — use column 0 (nearest available MaxT)
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
