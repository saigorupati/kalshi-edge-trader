# Kalshi Edge Trader

An autonomous algorithmic trading bot that identifies and exploits pricing inefficiencies in temperature prediction markets on [Kalshi](https://kalshi.com) — a CFTC-regulated prediction market exchange. The system trades daily high-temperature contracts for five major US cities using probabilistic weather modeling and a risk-managed Kelly Criterion position sizing strategy.

Built as a full-stack production system: Python trading engine + FastAPI backend + Next.js dashboard + AWS DynamoDB + Railway cloud hosting.

---

## How It Works

Temperature prediction markets price the probability that a city's daily high will fall within a specific degree range (e.g., "NYC high between 72–74°F"). When the market's implied probability diverges meaningfully from a calibrated probabilistic weather model, a tradeable edge exists.

**The core loop (every 30 minutes):**

1. **Download NOAA NBM Bulletin** — The National Blend of Models probabilistic forecast (~33MB) provides the 10th, 50th, and 90th temperature percentiles for each city
2. **Fit Normal Distribution** — Derives μ (mean) and σ (std dev) from the NBM percentiles: `σ = (P90 − P10) / (2 × 1.282)`
3. **Apply Calibration** — Per-city bias correction and sigma scaling learned from historical forecast vs. actual divergence, stored in DynamoDB
4. **Compute Market Probabilities** — For each Kalshi temperature bin, calculates `P(bin) = Φ((high − μ)/σ) − Φ((low − μ)/σ)` via SciPy
5. **Find Edge** — `net_edge = model_probability − ask_price − (fee_rate / ask_price)`. Trades only when net edge > 5%
6. **Find Bracket** — Additionally checks for adjacent bin pairs that straddle μ and pass stricter combined-edge and EV gates (see [Bracket Strategy](#bracket-strategy))
7. **Size Position** — Quarter-Kelly Criterion: `f* = (p − q) / (1 − q) × 0.25`, capped at 3% of balance per city per day
8. **Execute & Track** — Places RSA-signed orders on Kalshi, logs to DynamoDB with strategy tag, updates risk manager

**Target:** 1% daily compound growth, >90% win rate, maximum drawdown limited by 5% daily kill switch.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Railway Cloud                         │
│                                                             │
│  ┌──────────────────────────┐  ┌──────────────────────────┐ │
│  │   Service 1: Bot + API   │  │  Service 2: Dashboard    │ │
│  │                          │  │                          │ │
│  │  ┌────────────────────┐  │  │  Next.js 14 App Router  │ │
│  │  │  APScheduler       │  │  │  Tailwind CSS           │ │
│  │  │  (30-min cycle)    │  │  │  Recharts               │ │
│  │  └────────┬───────────┘  │  │  WebSocket client       │ │
│  │           │              │  └────────────┬─────────────┘ │
│  │  ┌────────▼───────────┐  │               │               │
│  │  │  Trading Engine    │  │      REST + WebSocket         │
│  │  │  weather.py        │  │               │               │
│  │  │  temperature.py    │◄─┼───────────────┘               │
│  │  │  edge.py           │  │                               │
│  │  │  sizing.py         │  │                               │
│  │  │  risk.py           │  │                               │
│  │  │  executor.py       │  │                               │
│  │  └────────┬───────────┘  │                               │
│  │           │              │                               │
│  │  ┌────────▼───────────┐  │                               │
│  │  │  FastAPI Server    │  │                               │
│  │  │  (port 8000)       │  │                               │
│  │  │  REST endpoints    │  │                               │
│  │  │  WebSocket /ws     │  │                               │
│  │  └────────────────────┘  │                               │
│  └──────────────────────────┘                               │
└─────────────────────────────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │    AWS DynamoDB     │
              │  kalshi-trades      │
              │  kalshi-calibration │
              │  kalshi-daily-pnl   │
              └─────────────────────┘
                         │
              ┌──────────▼──────────┐
              │   Kalshi Exchange   │
              │  (CFTC-regulated)   │
              └─────────────────────┘
```

---

## Features

### Trading Engine
- **NOAA NBM Integration** — Parses 33MB probabilistic forecast bulletins; downloads once per cycle, shared across all 5 city stations to avoid redundant network calls
- **Normal Distribution Pricing** — Computes bin probabilities via scipy cumulative distribution with per-city bias/sigma calibration
- **Quarter-Kelly Sizing** — Mathematically optimal position sizing scaled conservatively; caps exposure at 3% of balance per city per day
- **Single-Bin Edge Detection** — Minimum 5% net edge threshold after proportional fees; gates on ask price (5¢–95¢) and spread to avoid illiquid markets
- **Bracket Strategy** — Simultaneously runs a 2-bin straddle strategy alongside single-bin; both strategies tagged in DynamoDB for P&L comparison over time
- **Kill Switch** — Halts all trading automatically if daily loss exceeds 5% of day-start balance; resets at midnight UTC
- **Calibration Engine** — Learns and corrects per-city forecast bias from DynamoDB historical records at 09:00 daily
- **NWS Sanity Check** — Cross-references NBM forecast against NWS API before trading

### Dashboard
- **Bloomberg Terminal Aesthetic** — Dark theme with monospace fonts, color-coded P&L and risk signals
- **Password Protected** — Single-user httpOnly cookie auth; set `DASHBOARD_PASSWORD` env var
- **Live Open Positions** — Real-time table with unrealized P&L; EXIT button cancels live Kalshi order and marks trade resolved
- **WebSocket Push** — Bot broadcasts cycle updates to dashboard instantly; auto-reconnects with exponential backoff
- **Opportunity Scanner** — Shows all single-bin markets with edge, plus a collapsible bracket opportunities panel; ranked by net edge / EV with visual bar indicators
- **Equity Curve** — Recharts line chart with kill-switch event markers
- **City Forecasts** — μ, σ, bias correction, and best available edge per city
- **Risk Dashboard** — Position utilization bars, daily stop-loss meter, per-city exposure breakdown

### Infrastructure
- **AWS DynamoDB** — Pay-per-request NoSQL; TTL on all records; GSI for city+date queries; Decimal precision for financial data; trades tagged with `strategy` and `bracket_id` for A/B comparison
- **Railway PaaS** — Two services (bot+API, dashboard); auto-deploys from GitHub; zero ops overhead
- **APScheduler** — Background scheduler with 30-min trading cycle + daily calibration and PnL snapshot cron jobs
- **Paper Mode** — Full simulation without live orders; same code path, same DynamoDB records, same dashboard

---

## Tech Stack

| Layer | Technology |
|---|---|
| Trading Engine | Python 3.11, APScheduler |
| Weather Data | NOAA NBM (NOMADS), NWS API |
| Statistics | SciPy (normal distribution), NumPy |
| Exchange API | Kalshi REST API (RSA-PSS SHA-256 auth) |
| Backend API | FastAPI, Uvicorn, WebSockets |
| Database | AWS DynamoDB (boto3) |
| Dashboard | Next.js 14 (App Router), TypeScript |
| Styling | Tailwind CSS v3 |
| Charts | Recharts |
| Hosting | Railway (PaaS) |
| Cloud | AWS (DynamoDB only) |

---

## Project Structure

```
kalshi-edge-trader/
├── main.py                  # Entry point: APScheduler + bot initialization
├── config.py                # City configs, risk params, API constants
├── backtest.py              # Historical simulation against DynamoDB calibration records
├── kalshi_sample.py         # Kalshi API qualification sample (advanced tier)
│
├── data/
│   ├── weather.py           # NOAA NBM bulletin download + parse; NWS sanity check
│   └── kalshi.py            # RSA-PSS signed Kalshi API client; market + orderbook fetching
│
├── models/
│   ├── temperature.py       # Normal distribution fitting + bin probability computation
│   └── calibration.py       # Per-city bias correction + DynamoDB calibration updates
│
├── trading/
│   ├── edge.py              # Single-bin edge detection + BracketOpportunity logic
│   ├── sizing.py            # Quarter-Kelly position sizing
│   ├── risk.py              # RiskManager: kill switch, position limits, city exposure
│   └── executor.py          # Order placement: single-bin + bracket execution
│
├── portfolio/
│   └── tracker.py           # Balance sync, P&L tracking, win rate, daily snapshots
│
├── db/
│   └── dynamo.py            # DynamoDB client: trades (with strategy tag), calibration, P&L
│
├── api/
│   └── server.py            # FastAPI app: REST endpoints + WebSocket live feed
│
├── dashboard.py             # Rich CLI output + JSON cycle summary logging
│
└── frontend/                # Next.js dashboard
    ├── middleware.ts         # Auth: redirects unauthenticated requests to /login
    ├── app/
    │   ├── layout.tsx
    │   ├── page.tsx          # Redirects → /dashboard
    │   ├── login/
    │   │   ├── page.tsx      # Password login form
    │   │   └── actions.ts    # Server action: validates password, sets session cookie
    │   └── dashboard/
    │       └── page.tsx      # Main dashboard: polling + WebSocket state
    ├── components/
    │   ├── BalanceCard.tsx   # 4 stat cards + status bar
    │   ├── OpenPositions.tsx # Live positions table with EXIT button
    │   ├── EquityCurve.tsx   # Recharts equity curve with kill-switch markers
    │   ├── CityForecasts.tsx # Per-city μ/σ/bias/edge table
    │   ├── RiskStatus.tsx    # Kill switch, position bars, city exposure
    │   ├── RecentTrades.tsx  # Last 50 trades with W/L coloring
    │   └── OpportunityScanner.tsx  # Single-bin + bracket opportunity panels
    └── lib/
        ├── api.ts            # Typed fetch client for all API endpoints
        └── websocket.ts      # Auto-reconnect WebSocket hook (exponential backoff)
```

---

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- AWS account (free tier is sufficient)
- Kalshi account with Advanced API access

### 1. Clone & configure

```bash
git clone https://github.com/yourusername/kalshi-edge-trader.git
cd kalshi-edge-trader

cp .env.example .env
# Edit .env — add your Kalshi key, AWS credentials
```

### 2. Run the trading bot (paper mode)

```bash
pip install -r requirements.txt

TRADING_MODE=paper python main.py
# Logs every 30-min cycle to console
# API available at http://localhost:8000/api/docs
```

### 3. Run the dashboard

```bash
cd frontend
cp .env.local.example .env.local
# Set DASHBOARD_PASSWORD in .env.local
npm install
npm run dev
# Open http://localhost:3000  (redirects to /login, then /dashboard)
```

### 4. Run a backtest

```bash
python backtest.py --city NYC --days 30
python backtest.py --city LA --days 60
```

---

## Environment Variables

**Bot (`.env`):**

| Variable | Default | Description |
|---|---|---|
| `KALSHI_KEY_ID` | — | Kalshi API key ID |
| `KALSHI_PRIVATE_KEY_PEM` | — | RSA private key (PEM format) |
| `TRADING_MODE` | `paper` | `paper`, `demo`, or `live` |
| `STARTING_BALANCE` | `1000.0` | Starting balance in USD |
| `AWS_ACCESS_KEY_ID` | — | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | — | AWS credentials |
| `AWS_REGION` | `us-east-1` | DynamoDB region |
| `FRONTEND_URL` | `*` | Dashboard URL for CORS (e.g., https://dashboard.railway.app) |
| `MIN_EDGE_THRESHOLD` | `0.05` | Minimum net edge to enter a trade (0.03 = looser, 0.08 = strict) |
| `KELLY_FRACTION` | `0.25` | Fraction of full Kelly bet size (0.10 = tiny, 0.50 = half-Kelly) |

**Dashboard (`frontend/.env.local`):**

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_URL` | FastAPI backend URL (e.g., https://bot.railway.app) |
| `NEXT_PUBLIC_WS_URL` | WebSocket URL (e.g., wss://bot.railway.app) |
| `DASHBOARD_PASSWORD` | Password for the dashboard login page |

---

## API Reference

The FastAPI backend exposes a full REST API and WebSocket feed. Interactive docs available at `/api/docs`.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | Bot status, mode, cycle count |
| GET | `/api/balance` | Current balance, total return % |
| GET | `/api/positions/open` | Open positions with unrealized P&L |
| GET | `/api/trades?date=&city=` | Trades for a given date/city |
| GET | `/api/pnl/today` | Today's realized P&L, win/loss counts |
| GET | `/api/pnl/history` | All daily P&L records |
| GET | `/api/risk/status` | Kill switch, position limits, city exposure |
| GET | `/api/scanner` | Latest single-bin + bracket opportunities by city |
| GET | `/api/markets/{city}` | Live Kalshi markets for a city |
| GET | `/api/calibration/{city}` | Historical calibration records |
| DELETE | `/api/orders/{order_id}` | Cancel order + resolve trade |
| WS | `/ws/live` | Push: cycle updates, heartbeats |

---

## Strategy Details

### Cities Covered
Los Angeles (LA) · New York City (NYC) · Miami (MIA) · Chicago (CHI) · Phoenix (PHX)

### Single-Bin Edge Calculation
```
fee_cost  = KALSHI_FEE_RATE / ask_price     # proportional to premium, not flat
raw_edge  = model_probability − ask_price
net_edge  = raw_edge − fee_cost
Trade if  net_edge > MIN_EDGE_THRESHOLD AND 5¢ ≤ ask ≤ 95¢ AND spread ≤ 12¢
```

The fee is expressed as a fraction of the premium paid (not a flat 1¢), because Kalshi charges ~1% of *notional* ($0.01 per $1 contract). At a 5¢ ask, that's effectively a 20% fee on the premium — which is why markets priced below 5¢ are filtered out.

`MIN_EDGE_THRESHOLD` defaults to 5% but is tunable via env var. Setting it to 3% (~`0.03`) will let through more trades with thinner margins; be aware that at low ask prices the proportional fee consumes a large share of the edge.

### Bracket Strategy
```
profit_if_hit   = 1.0 − total_ask           # net payout if either leg wins
EV per contract = combined_prob − total_ask  # same form as single-bin
```

A bracket buys two adjacent bounded bins that straddle μ. All three gates must pass:

1. Each leg individually: `net_edge ≥ 5%`
2. `total_net_edge ≥ 10%`
3. `EV > 0` i.e. `combined_model_prob > total_ask`

Both strategies run simultaneously each cycle. Every trade is tagged `strategy="single"` or `strategy="bracket"` in DynamoDB, with bracket legs sharing a `bracket_id` UUID. This enables P&L comparison across strategies over the paper-trading period.

### Kelly Criterion Sizing
```
f*    = (p − q) / (1 − q)                    # full Kelly
size  = f* × KELLY_FRACTION                   # default: quarter-Kelly (0.25)
cap   = min(size, 3% of balance)              # per-city daily cap
```
Where `p` = model probability, `q` = 1 − p. `KELLY_FRACTION` defaults to `0.25` and is tunable via env var.

For bracket trades, the city budget is split evenly across both legs (`per_leg_budget = city_remaining / 2`).

### Risk Controls
- **Kill switch** — 5% max daily loss; halts all trading until next UTC day
- **Position cap** — Max 10 simultaneous open positions
- **City cap** — 3% of balance maximum exposure per city per day
- **Ask gates** — 5¢ min (fee protection) and 95¢ max (no edge on near-certain markets)
- **Spread gate** — Skip markets with bid/ask spread > 12¢ (illiquid)
- **Paper mode** — Identical code path, zero real capital at risk

### Calibration
Each morning at 09:00 UTC, the system:
1. Looks up yesterday's actual high temperature (NWS historical data)
2. Computes forecast error: `bias = actual − nbm_mu`
3. Updates per-city bias correction and sigma scale in DynamoDB
4. Applies corrections to today's model immediately

---

## DynamoDB Schema Notes

Every trade record in `kalshi-trades` includes:
- `strategy` — `"single"` or `"bracket"` (defaults to `"single"` for older records)
- `bracket_id` — UUID string shared by both legs of a bracket; absent on single-bin trades

This allows filtering by strategy after a paper-trading period to compare win rates and P&L:

```python
# Example: filter DynamoDB scan by strategy
trades = db.get_daily_trades("2026-03-01")
single_trades  = [t for t in trades if t.get("strategy") == "single"]
bracket_trades = [t for t in trades if t.get("strategy") == "bracket"]
```

---

## Deployment (Railway)

Two services in one Railway project:

**Service 1 — Bot + API** (root directory `/`):
- Runs `python main.py` via `Procfile`
- FastAPI on port 8000 (auto-exposed by Railway)

**Service 2 — Dashboard** (root directory `/frontend`):
- Builds with `npm run build`, starts with `npm start`
- Next.js on port 3000
- Set `DASHBOARD_PASSWORD` in Railway → Service 2 → Variables

Set environment variables in Railway dashboard. Both services deploy automatically on `git push`.

---

## Disclaimer

This project is for educational and research purposes. Prediction market trading involves financial risk. Past performance does not guarantee future results. Always start in paper mode and understand the strategy fully before trading real capital. This is not financial advice.

---

## License

MIT
