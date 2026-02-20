import os
from dataclasses import dataclass, field
from typing import Dict
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Kalshi API
# ---------------------------------------------------------------------------
DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
PROD_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

KALSHI_KEY_ID: str = os.getenv("KALSHI_KEY_ID", "PLACEHOLDER_KEY_ID")
KALSHI_PRIVATE_KEY_PEM: str = os.getenv("KALSHI_PRIVATE_KEY_PEM", "PLACEHOLDER_PEM")
TRADING_MODE: str = os.getenv("TRADING_MODE", "paper")  # paper | demo | live

# ---------------------------------------------------------------------------
# NBM / NWS
# ---------------------------------------------------------------------------
NBM_BASE_URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod"
NBM_CYCLES = ["19", "13", "07", "01"]  # Preferred order (most recent first)
NBM_CYCLE_LAG_HOURS = 2  # Data available ~2h after cycle time

NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"

# ---------------------------------------------------------------------------
# AWS DynamoDB
# ---------------------------------------------------------------------------
AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")

DYNAMO_CALIBRATION_TABLE = "kalshi-calibration"
DYNAMO_TRADES_TABLE = "kalshi-trades"
DYNAMO_DAILY_PNL_TABLE = "kalshi-daily-pnl"
CALIBRATION_TTL_DAYS = 90
TRADES_TTL_DAYS = 365


# ---------------------------------------------------------------------------
# City definitions
# ---------------------------------------------------------------------------
@dataclass
class CityConfig:
    name: str
    display_name: str
    lat: float
    lon: float
    nbm_station: str       # ICAO station ID for NBM bulletin (e.g. KLAX)
    kalshi_series: str     # Kalshi series ticker (e.g. KXHIGHLAX)
    nws_office: str        # NWS office code
    nws_grid_x: int
    nws_grid_y: int
    # Calibration params — updated at runtime from DynamoDB history
    bias_correction: float = 0.0   # degrees F added to NBM median
    sigma_scale: float = 1.0       # multiplier on NBM std dev


CITIES: Dict[str, CityConfig] = {
    "LA": CityConfig(
        name="LA",
        display_name="Los Angeles",
        lat=34.0522,
        lon=-118.2437,
        nbm_station="KLAX",
        kalshi_series="KXHIGHLAX",
        nws_office="LOX",
        nws_grid_x=155,
        nws_grid_y=45,
    ),
    "NYC": CityConfig(
        name="NYC",
        display_name="New York City",
        lat=40.7829,
        lon=-73.9654,
        nbm_station="KNYC",
        kalshi_series="KXHIGHNY",
        nws_office="OKX",
        nws_grid_x=33,
        nws_grid_y=37,
    ),
    "MIA": CityConfig(
        name="MIA",
        display_name="Miami",
        lat=25.7617,
        lon=-80.1918,
        nbm_station="KMIA",
        kalshi_series="KXHIGHMIA",
        nws_office="MFL",
        nws_grid_x=110,
        nws_grid_y=37,
    ),
    "CHI": CityConfig(
        name="CHI",
        display_name="Chicago",
        lat=41.8781,
        lon=-87.6298,
        nbm_station="KORD",
        kalshi_series="KXHIGHCHI",
        nws_office="LOT",
        nws_grid_x=74,
        nws_grid_y=74,
    ),
    "PHX": CityConfig(
        name="PHX",
        display_name="Phoenix",
        lat=33.4484,
        lon=-112.0740,
        nbm_station="KPHX",
        kalshi_series="KXHIGHTPHX",
        nws_office="PSR",
        nws_grid_x=159,
        nws_grid_y=58,
    ),
}

# ---------------------------------------------------------------------------
# Risk parameters
# ---------------------------------------------------------------------------
STARTING_BALANCE: float = float(os.getenv("STARTING_BALANCE", "1000.0"))
MAX_POSITION_PCT_PER_CITY: float = 0.03   # 3% of balance max risk per city/day
MAX_OPEN_POSITIONS: int = 5               # max simultaneous open positions
DAILY_STOP_LOSS_PCT: float = 0.05         # -5% triggers kill switch for the day
MIN_EDGE_THRESHOLD: float = 0.05          # minimum net edge required to trade
KELLY_FRACTION: float = 0.25             # fraction of full Kelly (safety)
KALSHI_FEE_RATE: float = 0.01            # ~1% fee per contract (conservative)

# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
LOOP_INTERVAL_MINUTES: int = 30

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
