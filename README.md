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
5. **Find Edge** — `net_edge = model_probability − ask_price − fee_rate`. Trades only when net edge > 5%
6. **Size Position** — Quarter-Kelly Criterion: `f* = (p − q) / (1 − q) × 0.25`, capped at 3% of balance per city per day
7. **Execute & Track** — Places RSA-signed orders on Kalshi, logs to DynamoDB, updates risk manager

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
- **Edge Detection** — Minimum 5% net edge threshold after fees to enter a position; focuses on the edges of a ~4°F bracket where model confidence is highest
- **Kill Switch** — Halts all trading automatically if daily loss exceeds 5% of day-start balance; resets at midnight UTC
- **Calibration Engine** — Learns and corrects per-city forecast bias from DynamoDB historical records at 09:00 daily
- **NWS Sanity Check** — Cross-references NBM forecast against NWS API before trading

### Dashboard
- **Bloomberg Terminal Aesthetic** — Dark theme with monospace fonts, color-coded P&L and risk signals
- **Live Open Positions** — Real-time table with unrealized P&L; EXIT button cancels live Kalshi order and marks trade resolved
- **WebSocket Push** — Bot broadcasts cycle updates to dashboard instantly; auto-reconnects with exponential backoff
- **Opportunity Scanner** — Shows all markets with edge > threshold, ranked by net edge with visual bar indicators
- **Equity Curve** — Recharts line chart with kill-switch event markers
- **City Forecasts** — μ, σ, bias correction, and best available edge per city
- **Risk Dashboard** — Position utilization bars, daily stop-loss meter, per-city exposure breakdown

### Infrastructure
- **AWS DynamoDB** — Pay-per-request NoSQL; TTL on all records; GSI for city+date queries; Decimal precision for financial data
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
│   └── kalshi.py            # RSA-PSS signed Kalshi API client
│
├── models/
│   ├── temperature.py       # Normal distribution fitting + bin probability computation
│   └── calibration.py       # Per-city bias correction + DynamoDB calibration updates
│
├── trading/
│   ├── edge.py              # Edge detection: model_prob − ask − fees
│   ├── sizing.py            # Quarter-Kelly position sizing
│   ├── risk.py              # RiskManager: kill switch, position limits, city exposure
│   └── executor.py          # Order placement orchestration
│
├── portfolio/
│   └── tracker.py           # Balance sync, P&L tracking, win rate, daily snapshots
│
├── db/
│   └── dynamo.py            # DynamoDB client: trades, calibration, daily P&L tables
│
├── api/
│   └── server.py            # FastAPI app: REST endpoints + WebSocket live feed
│
├── dashboard.py             # Rich CLI output + JSON cycle summary logging
│
└── frontend/                # Next.js dashboard
    ├── app/
    │   ├── layout.tsx
    │   ├── page.tsx          # Redirects → /dashboard
    │   └── dashboard/
    │       └── page.tsx      # Main dashboard: polling + WebSocket state
    ├── components/
    │   ├── BalanceCard.tsx   # 4 stat cards + status bar
    │   ├── OpenPositions.tsx # Live positions table with EXIT button
    │   ├── EquityCurve.tsx   # Recharts equity curve with kill-switch markers
    │   ├── CityForecasts.tsx # Per-city μ/σ/bias/edge table
    │   ├── RiskStatus.tsx    # Kill switch, position bars, city exposure
    │   ├── RecentTrades.tsx  # Last 50 trades with W/L coloring
    │   └── OpportunityScanner.tsx  # Ranked edge opportunities with visual bars
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
npm install
npm run dev
# Open http://localhost:3000/dashboard
```

### 4. Run a backtest

```bash
python backtest.py --city NYC --days 30
python backtest.py --city LA --days 60
```

---

## Environment Variables

**Bot (`.env`):**

| Variable | Description |
|---|---|
| `KALSHI_KEY_ID` | Kalshi API key ID |
| `KALSHI_PRIVATE_KEY_PEM` | RSA private key (PEM format) |
| `TRADING_MODE` | `paper` or `live` |
| `STARTING_BALANCE` | Starting balance in USD (default: 1000) |
| `AWS_ACCESS_KEY_ID` | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials |
| `AWS_REGION` | DynamoDB region (default: us-east-1) |
| `FRONTEND_URL` | Dashboard URL for CORS (e.g., https://dashboard.railway.app) |

**Dashboard (`.env.local`):**

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_URL` | FastAPI backend URL (e.g., https://bot.railway.app) |
| `NEXT_PUBLIC_WS_URL` | WebSocket URL (e.g., wss://bot.railway.app) |

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
| GET | `/api/scanner` | Latest opportunities by city |
| GET | `/api/markets/{city}` | Live Kalshi markets for a city |
| GET | `/api/calibration/{city}` | Historical calibration records |
| DELETE | `/api/orders/{order_id}` | Cancel order + resolve trade |
| WS | `/ws/live` | Push: cycle updates, heartbeats |

---

## Strategy Details

### Cities Covered
Los Angeles (LA) · New York City (NYC) · Miami (MIA) · Chicago (CHI) · San Francisco (SF)

### Edge Calculation
```
raw_edge  = model_probability − ask_price
net_edge  = raw_edge − fee_rate (1%)
Trade if  net_edge > 5%
```

### Kelly Criterion Sizing
```
f*    = (p − q) / (1 − q)          # full Kelly
size  = f* × 0.25                   # quarter-Kelly (conservative)
cap   = min(size, 3% of balance)    # per-city daily cap
```
Where `p` = model probability, `q` = 1 − p.

### Risk Controls
- **Kill switch** — 5% max daily loss; halts all trading until next UTC day
- **Position cap** — Max 10 simultaneous open positions
- **City cap** — 3% of balance maximum exposure per city per day
- **Paper mode** — Identical code path, zero real capital at risk

### Calibration
Each morning at 09:00 UTC, the system:
1. Looks up yesterday's actual high temperature (NWS historical data)
2. Computes forecast error: `bias = actual − nbm_mu`
3. Updates per-city bias correction and sigma scale in DynamoDB
4. Applies corrections to today's model immediately

---

## Deployment (Railway)

Two services in one Railway project:

**Service 1 — Bot + API** (root directory `/`):
- Runs `python main.py` via `Procfile`
- FastAPI on port 8000 (auto-exposed by Railway)

**Service 2 — Dashboard** (root directory `/frontend`):
- Builds with `npm run build`, starts with `npm start`
- Next.js on port 3000

Set environment variables in Railway dashboard. Both services deploy automatically on `git push`.

Full deployment guide in the [hosting section](#quick-start).

---

## Disclaimer

This project is for educational and research purposes. Prediction market trading involves financial risk. Past performance does not guarantee future results. Always start in paper mode and understand the strategy fully before trading real capital. This is not financial advice.

---

## License

MIT
