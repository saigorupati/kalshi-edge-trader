"""
Dashboard for the Kalshi trading bot.

In a hosted Railway environment, we emit structured log lines (JSON-like)
that are readable in Railway's log viewer.

Also provides a Rich table summary callable from main.py.
"""

import json
import logging
import datetime
from typing import Dict, List, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from config import TRADING_MODE, CITIES
from trading.edge import TradeOpportunity
from portfolio.tracker import PortfolioTracker

logger = logging.getLogger(__name__)
console = Console()


def log_cycle_summary(
    opportunities_by_city: Dict[str, List[TradeOpportunity]],
    dist_by_city: dict,
    executed_by_city: Dict[str, list],
    balance: float,
    cycle_num: int,
) -> None:
    """Emit a structured log line summarizing this cycle."""
    city_summaries = {}
    for city_code, opps in opportunities_by_city.items():
        dist = dist_by_city.get(city_code)
        best_edge = max((o.net_edge for o in opps), default=0.0)
        viable = [o for o in opps if o.has_edge]
        city_summaries[city_code] = {
            "mu": round(dist.mu, 1) if dist else None,
            "sigma": round(dist.sigma, 1) if dist else None,
            "markets_evaluated": len(opps),
            "viable_opportunities": len(viable),
            "best_edge_pct": round(best_edge * 100, 1),
            "trades_placed": len(executed_by_city.get(city_code, [])),
        }

    log_entry = {
        "event": "cycle_complete",
        "cycle": cycle_num,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "balance": round(balance, 2),
        "mode": TRADING_MODE,
        "cities": city_summaries,
    }
    # Use print so it always appears in Railway logs even if logging level is WARN
    print(json.dumps(log_entry))


def build_opportunity_table(
    opportunities_by_city: Dict[str, List[TradeOpportunity]],
    dist_by_city: dict,
) -> Table:
    """Build a Rich table showing all evaluated markets and their edges."""
    table = Table(
        title=f"Kalshi Temperature Edge Scanner — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
    )
    table.add_column("City", style="bold cyan", width=6)
    table.add_column("Ticker", width=38)
    table.add_column("Range", width=16)
    table.add_column("Model P%", justify="right", width=9)
    table.add_column("Ask", justify="right", width=6)
    table.add_column("Bid", justify="right", width=6)
    table.add_column("Net Edge%", justify="right", width=10)
    table.add_column("Action", width=10)

    for city_code in CITIES:
        opps = opportunities_by_city.get(city_code, [])
        dist = dist_by_city.get(city_code)

        if not opps:
            table.add_row(
                city_code,
                "—",
                "No data",
                "—", "—", "—", "—",
                "[dim]NO DATA[/dim]",
            )
            continue

        for opp in opps[:6]:  # Show top 6 per city
            edge_pct = opp.net_edge * 100
            model_pct = opp.model_prob * 100

            if opp.has_edge:
                action_str = "[bold green]BUY YES[/bold green]"
                edge_str = f"[bold green]+{edge_pct:.1f}%[/bold green]"
            elif edge_pct > -2:
                action_str = "[yellow]WATCH[/yellow]"
                edge_str = f"[yellow]{edge_pct:.1f}%[/yellow]"
            else:
                action_str = "[dim]SKIP[/dim]"
                edge_str = f"[dim]{edge_pct:.1f}%[/dim]"

            table.add_row(
                city_code,
                opp.market.ticker,
                opp.market.yes_sub_title or "?",
                f"{model_pct:.1f}%",
                f"{opp.ask_price:.2f}",
                f"{opp.bid_price:.2f}",
                edge_str,
                action_str,
            )

    return table


def build_portfolio_panel(tracker: PortfolioTracker, risk_status: dict) -> Panel:
    """Build a panel showing portfolio stats."""
    summary = tracker.get_daily_summary()
    returns = tracker.compute_compounded_returns()
    win_rate = tracker.get_win_rate(lookback_days=30)

    lines = [
        f"Balance:       ${summary['balance']:>9.2f}   Mode: [bold]{TRADING_MODE.upper()}[/bold]",
        f"Daily PnL:     {('$' + format(summary['realized_pnl'], '+.2f')) if summary['realized_pnl'] is not None else 'N/A':>10}",
        f"Open Positions:{summary['open_positions']:>4}  Trades Today: {summary['total_trades']}",
        f"W/L Today:     {summary['wins']}W / {summary['losses']}L   "
        f"30d Win Rate: {f'{win_rate*100:.0f}%' if win_rate is not None else 'N/A'}",
        f"Total Return:  {returns['total_return_pct']:>+.1f}%  Days Running: {returns['days_running']}",
        f"Kill Switch:   {'[bold red]ACTIVE[/bold red]' if risk_status.get('kill_switch') else '[green]OFF[/green]'}   "
        f"Open Positions: {risk_status.get('open_positions', 0)}/{risk_status.get('max_positions', 5)}",
    ]

    return Panel(
        "\n".join(lines),
        title="Portfolio",
        border_style="blue",
    )


def print_cycle_report(
    opportunities_by_city: Dict[str, List[TradeOpportunity]],
    dist_by_city: dict,
    executed_by_city: Dict[str, list],
    tracker: PortfolioTracker,
    risk_manager,
    cycle_num: int,
) -> None:
    """Print a full cycle report to the terminal (visible in Railway logs)."""
    console.rule(f"[bold]Cycle #{cycle_num} — {datetime.datetime.now().strftime('%H:%M:%S UTC')}[/bold]")

    # Opportunity table
    table = build_opportunity_table(opportunities_by_city, dist_by_city)
    console.print(table)

    # Portfolio panel
    panel = build_portfolio_panel(tracker, risk_manager.status_summary())
    console.print(panel)

    # Executed trades summary
    total_executed = sum(len(v) for v in executed_by_city.values())
    if total_executed > 0:
        console.print(f"[bold green]Placed {total_executed} order(s) this cycle.[/bold green]")
    else:
        console.print("[dim]No orders placed this cycle.[/dim]")

    console.rule()
