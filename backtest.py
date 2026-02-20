"""
Backtest simulator for the Kalshi temperature edge trading strategy.

Uses historical calibration records from DynamoDB (which store NBM forecasts
and actual settlement highs) to simulate trading performance.

Usage:
  python backtest.py
  python backtest.py --city LA --days 30
"""

import argparse
import datetime
import logging
import math
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.table import Table
from rich import box

from config import CITIES, STARTING_BALANCE, MIN_EDGE_THRESHOLD, KELLY_FRACTION, KALSHI_FEE_RATE
from db.dynamo import DynamoClient
from models.temperature import bin_probability
from trading.sizing import kelly_fraction as compute_kelly, compute_contract_count

logger = logging.getLogger(__name__)
console = Console()


def simulate_kalshi_markets(
    mu: float, sigma: float, step: float = 1.0, range_width: float = 10.0
) -> List[Tuple[Optional[float], Optional[float], bool, bool, float]]:
    """
    Generates synthetic Kalshi markets around the forecast mean.
    Each market is a 1°F bin centered near mu.
    Returns list of (temp_low, temp_high, is_open_low, is_open_high, simulated_ask).

    The simulated_ask approximates what Kalshi's market would price —
    we use a simplified assumption that the market is priced at the
    "true" probability (under a wider sigma = sigma+2°F to represent
    market uncertainty), plus a 2% spread buffer.
    """
    import numpy as np
    from scipy import stats

    # Simulate market's "own" distribution (slightly less confident than our model)
    market_sigma = sigma + 2.0
    market_dist = stats.norm(loc=mu, scale=market_sigma)

    bins = []
    lo = math.floor(mu - range_width)
    hi = math.ceil(mu + range_width)

    for t in range(int(lo), int(hi)):
        t_low = float(t)
        t_high = float(t + step)
        # Market ask = market's probability + 1% spread (market maker edge)
        mkt_prob = market_dist.cdf(t_high) - market_dist.cdf(t_low)
        ask = min(0.97, max(0.03, mkt_prob + 0.01))
        bins.append((t_low, t_high, False, False, ask))

    # Open-ended bins
    bins.insert(0, (None, lo, True, False, min(0.97, market_dist.cdf(lo) + 0.01)))
    bins.append((hi, None, False, True, min(0.97, 1.0 - market_dist.cdf(hi) + 0.01)))
    return bins


def simulate_trading_day(
    city: str,
    nbm_mu: float,
    nbm_sigma: float,
    actual_high: float,
    balance: float,
    bias_correction: float = 0.0,
    sigma_scale: float = 1.0,
) -> dict:
    """
    Simulates one day of trading for a city.
    Returns dict with: pnl, win, trade_placed, edge_used, kelly_frac.
    """
    adj_mu = nbm_mu + bias_correction
    adj_sigma = max(nbm_sigma * sigma_scale, 1.0)

    markets = simulate_kalshi_markets(adj_mu, adj_sigma)
    best_edge = -999.0
    best_trade = None

    for t_low, t_high, is_open_low, is_open_high, ask in markets:
        model_prob = bin_probability(adj_mu, adj_sigma, t_low, t_high, is_open_low, is_open_high)
        raw_edge = model_prob - ask
        net_edge = raw_edge - KALSHI_FEE_RATE

        if net_edge > best_edge:
            best_edge = net_edge
            best_trade = {
                "temp_low": t_low,
                "temp_high": t_high,
                "is_open_low": is_open_low,
                "is_open_high": is_open_high,
                "ask": ask,
                "model_prob": model_prob,
                "net_edge": net_edge,
            }

    if best_trade is None or best_edge < MIN_EDGE_THRESHOLD:
        return {"pnl": 0.0, "win": None, "trade_placed": False, "edge": best_edge, "kelly": 0.0}

    t = best_trade
    k_frac = compute_kelly(t["model_prob"], t["ask"])
    max_risk = 0.03 * balance
    count, dollar_risk = compute_contract_count(k_frac, balance, t["ask"], max_risk)

    if count < 1:
        return {"pnl": 0.0, "win": None, "trade_placed": False, "edge": best_edge, "kelly": k_frac}

    # Did YES resolve?
    if t["is_open_low"] and t["temp_high"] is not None:
        yes_resolved = actual_high <= t["temp_high"]
    elif t["is_open_high"] and t["temp_low"] is not None:
        yes_resolved = actual_high >= t["temp_low"]
    elif t["temp_low"] is not None and t["temp_high"] is not None:
        yes_resolved = t["temp_low"] <= actual_high <= t["temp_high"]
    else:
        yes_resolved = False

    if yes_resolved:
        pnl = (1.0 - t["ask"]) * count
    else:
        pnl = -t["ask"] * count

    return {
        "pnl": pnl,
        "win": yes_resolved,
        "trade_placed": True,
        "edge": best_edge,
        "kelly": k_frac,
        "dollar_risk": dollar_risk,
        "ask": t["ask"],
        "model_prob": t["model_prob"],
    }


def run_backtest(
    city_filter: Optional[str] = None,
    lookback_days: int = 30,
    initial_balance: float = STARTING_BALANCE,
) -> dict:
    """
    Full historical simulation across all cities using DynamoDB calibration records.
    """
    db = DynamoClient()
    cities = {k: v for k, v in CITIES.items() if city_filter is None or k == city_filter}

    total_trades = 0
    total_wins = 0
    total_pnl = 0.0
    balance = initial_balance
    daily_returns = []

    all_results = []

    for city_code, city_cfg in cities.items():
        records = db.get_calibration_history(city_code, lookback_days=lookback_days)
        if not records:
            logger.warning("No calibration history for %s", city_code)
            continue

        for rec in sorted(records, key=lambda r: r["forecast_date"]):
            result = simulate_trading_day(
                city=city_code,
                nbm_mu=rec["nbm_mu"],
                nbm_sigma=rec["nbm_sigma"],
                actual_high=rec["actual_high"],
                balance=balance,
                bias_correction=city_cfg.bias_correction,
                sigma_scale=city_cfg.sigma_scale,
            )
            result["city"] = city_code
            result["date"] = rec["forecast_date"]
            all_results.append(result)

            if result["trade_placed"]:
                total_trades += 1
                balance += result["pnl"]
                total_pnl += result["pnl"]
                if result["win"]:
                    total_wins += 1
                daily_returns.append(result["pnl"] / (balance - result["pnl"]) if balance > result["pnl"] else 0)

    win_rate = total_wins / total_trades if total_trades > 0 else 0.0
    total_return = (balance - initial_balance) / initial_balance

    import numpy as np
    max_drawdown = 0.0
    peak = initial_balance
    running = initial_balance
    for r in sorted(all_results, key=lambda x: x["date"]):
        if r["trade_placed"]:
            running += r["pnl"]
            peak = max(peak, running)
            drawdown = (peak - running) / peak
            max_drawdown = max(max_drawdown, drawdown)

    return {
        "initial_balance": initial_balance,
        "final_balance": balance,
        "total_return_pct": total_return * 100,
        "total_trades": total_trades,
        "win_rate": win_rate * 100,
        "total_pnl": total_pnl,
        "max_drawdown_pct": max_drawdown * 100,
        "daily_results": all_results,
    }


def print_backtest_report(results: dict) -> None:
    summary = Table(title="Backtest Results", box=box.SIMPLE_HEAVY)
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", justify="right", style="bold")

    summary.add_row("Initial Balance", f"${results['initial_balance']:.2f}")
    summary.add_row("Final Balance", f"${results['final_balance']:.2f}")
    summary.add_row(
        "Total Return",
        f"[{'green' if results['total_return_pct'] > 0 else 'red'}]{results['total_return_pct']:+.2f}%[/]",
    )
    summary.add_row("Total Trades", str(results["total_trades"]))
    summary.add_row(
        "Win Rate",
        f"[{'green' if results['win_rate'] >= 70 else 'yellow'}]{results['win_rate']:.1f}%[/]",
    )
    summary.add_row("Max Drawdown", f"[red]{results['max_drawdown_pct']:.1f}%[/]")
    summary.add_row("Total PnL", f"${results['total_pnl']:+.2f}")

    console.print(summary)

    if results["win_rate"] >= 70:
        console.print("\n[bold green]Strategy looks viable for live trading consideration.[/bold green]")
    elif results["win_rate"] >= 55:
        console.print("\n[yellow]Win rate marginal — consider increasing edge threshold.[/yellow]")
    else:
        console.print("\n[bold red]Win rate too low — do NOT go live yet.[/bold red]")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Backtest the Kalshi temperature trading strategy")
    parser.add_argument("--city", help="City code to backtest (e.g. LA, NYC). Default: all cities.")
    parser.add_argument("--days", type=int, default=30, help="Lookback days (default: 30)")
    parser.add_argument("--balance", type=float, default=STARTING_BALANCE, help="Starting balance")
    args = parser.parse_args()

    console.print(f"\n[bold]Running backtest: {args.days} days | city={args.city or 'ALL'} | balance=${args.balance:.0f}[/bold]\n")

    results = run_backtest(
        city_filter=args.city,
        lookback_days=args.days,
        initial_balance=args.balance,
    )
    print_backtest_report(results)
