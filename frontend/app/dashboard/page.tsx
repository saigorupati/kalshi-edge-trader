'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import {
  api,
  BalanceData,
  OpenPosition,
  Trade,
  PnLToday,
  PnLRecord,
  RiskStatus,
  ScannerState,
} from '@/lib/api';
import { useWebSocket, LiveUpdate } from '@/lib/websocket';

import BalanceCard      from '@/components/BalanceCard';
import OpenPositions    from '@/components/OpenPositions';
import EquityCurve      from '@/components/EquityCurve';
import CityForecasts    from '@/components/CityForecasts';
import RiskStatus       from '@/components/RiskStatus';
import RecentTrades     from '@/components/RecentTrades';
import OpportunityScanner from '@/components/OpportunityScanner';

// ── State interface ────────────────────────────────────────────────
interface DashboardState {
  balance:     BalanceData | null;
  positions:   OpenPosition[];
  trades:      Trade[];
  pnlToday:    PnLToday | null;
  pnlHistory:  PnLRecord[];
  risk:        RiskStatus | null;
  scanner:     ScannerState | null;
  lastUpdated: Date | null;
  loading:     boolean;
  error:       string | null;
}

// ── Polling intervals (ms) ────────────────────────────────────────
const BALANCE_INTERVAL  = 30_000;
const POSITION_INTERVAL = 10_000;
const TRADES_INTERVAL   = 60_000;
const HISTORY_INTERVAL  = 120_000;

export default function DashboardPage() {
  const [state, setState] = useState<DashboardState>({
    balance: null, positions: [], trades: [], pnlToday: null,
    pnlHistory: [], risk: null, scanner: null,
    lastUpdated: null, loading: true, error: null,
  });

  const mountedRef = useRef(true);

  function patch(partial: Partial<DashboardState>) {
    if (mountedRef.current) setState((prev) => ({ ...prev, ...partial }));
  }

  // ── Fetch helpers ───────────────────────────────────────────────

  const fetchBalance = useCallback(async () => {
    try {
      const [balance, pnlToday] = await Promise.all([api.balance(), api.pnlToday()]);
      patch({ balance, pnlToday, lastUpdated: new Date() });
    } catch { /* silently retry */ }
  }, []);

  const fetchPositions = useCallback(async () => {
    try {
      const [positions, risk] = await Promise.all([api.openPositions(), api.riskStatus()]);
      patch({ positions, risk });
    } catch { /* silently retry */ }
  }, []);

  const fetchTrades = useCallback(async () => {
    try {
      const trades = await api.trades();
      patch({ trades });
    } catch { /* silently retry */ }
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const [pnlHistory, scanner] = await Promise.all([api.pnlHistory(), api.scanner()]);
      patch({ pnlHistory, scanner });
    } catch { /* silently retry */ }
  }, []);

  const fetchAll = useCallback(async () => {
    try {
      const [balance, positions, trades, pnlToday, pnlHistory, risk, scanner] =
        await Promise.allSettled([
          api.balance(),
          api.openPositions(),
          api.trades(),
          api.pnlToday(),
          api.pnlHistory(),
          api.riskStatus(),
          api.scanner(),
        ]);

      patch({
        balance:    balance.status    === 'fulfilled' ? balance.value    : null,
        positions:  positions.status  === 'fulfilled' ? positions.value  : [],
        trades:     trades.status     === 'fulfilled' ? trades.value     : [],
        pnlToday:   pnlToday.status   === 'fulfilled' ? pnlToday.value   : null,
        pnlHistory: pnlHistory.status === 'fulfilled' ? pnlHistory.value : [],
        risk:       risk.status       === 'fulfilled' ? risk.value       : null,
        scanner:    scanner.status    === 'fulfilled' ? scanner.value    : null,
        loading: false,
        lastUpdated: new Date(),
        error: null,
      });
    } catch (e: unknown) {
      patch({ loading: false, error: e instanceof Error ? e.message : 'Failed to load' });
    }
  }, []);

  // ── WebSocket: push updates from the bot ───────────────────────

  const handleWsMessage = useCallback((msg: LiveUpdate) => {
    if (msg.type === 'cycle_update' || msg.type === 'snapshot') {
      // Refresh positions + balance immediately on each bot cycle
      fetchBalance();
      fetchPositions();
      fetchTrades();
      // Update scanner from the WS payload itself if included
      if (msg.opportunities !== undefined) {
        setState((prev) => ({
          ...prev,
          scanner: {
            cycle_number: msg.cycle_number ?? prev.scanner?.cycle_number ?? 0,
            last_updated: msg.timestamp,
            opportunities: msg.opportunities as ScannerState['opportunities'],
            city_distributions: (msg.city_distributions as ScannerState['city_distributions']) ?? prev.scanner?.city_distributions ?? {},
          },
        }));
      }
    }
  }, [fetchBalance, fetchPositions, fetchTrades]);

  const { status: wsStatus, lastHeartbeat } = useWebSocket({ onMessage: handleWsMessage });

  // ── Mount: initial fetch + polling ────────────────────────────

  useEffect(() => {
    mountedRef.current = true;
    fetchAll();

    const t1 = setInterval(fetchBalance,  BALANCE_INTERVAL);
    const t2 = setInterval(fetchPositions, POSITION_INTERVAL);
    const t3 = setInterval(fetchTrades,   TRADES_INTERVAL);
    const t4 = setInterval(fetchHistory,  HISTORY_INTERVAL);

    return () => {
      mountedRef.current = false;
      clearInterval(t1); clearInterval(t2);
      clearInterval(t3); clearInterval(t4);
    };
  }, [fetchAll, fetchBalance, fetchPositions, fetchTrades, fetchHistory]);

  // ── Exit callback ─────────────────────────────────────────────

  const onExitSuccess = useCallback(() => {
    fetchPositions();
    fetchBalance();
    fetchTrades();
  }, [fetchPositions, fetchBalance, fetchTrades]);

  // ── Render ────────────────────────────────────────────────────

  const { balance, positions, trades, pnlToday, pnlHistory, risk, scanner, lastUpdated, loading, error } = state;
  const mode = balance?.mode ?? 'paper';

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-primary">
        <div className="text-center space-y-3">
          <div className="dot-yellow w-4 h-4 mx-auto animate-pulse" />
          <p className="text-text-secondary font-mono text-sm">Connecting to trading bot…</p>
        </div>
      </div>
    );
  }

  if (error && !balance) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-primary">
        <div className="card p-6 max-w-sm text-center space-y-3">
          <p className="text-accent-red font-mono text-sm">{error}</p>
          <p className="text-text-muted text-xs">
            Make sure the FastAPI backend is running on{' '}
            <code className="text-accent-cyan">
              {process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}
            </code>
          </p>
          <button className="btn-cyan mt-2" onClick={() => { patch({ loading: true }); fetchAll(); }}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bg-primary text-text-primary">
      {/* ── Header ── */}
      <header className="border-b border-bg-border bg-bg-secondary sticky top-0 z-40">
        <div className="max-w-[1600px] mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-accent-cyan font-mono font-bold tracking-wider text-base">
              KALSHI EDGE TRADER
            </span>
            <span className={`badge ${mode === 'live' ? 'badge-red' : mode === 'demo' ? 'badge-yellow' : 'badge-cyan'}`}>
              {mode.toUpperCase()}
            </span>
          </div>
          <div className="flex items-center gap-4 text-xs font-mono text-text-muted">
            <span
              className={`flex items-center gap-1.5 ${
                wsStatus === 'connected' ? 'text-accent-green' : 'text-accent-yellow'
              }`}
            >
              <span className={wsStatus === 'connected' ? 'dot-green' : 'dot-yellow'} />
              WS {wsStatus}
            </span>
            {lastHeartbeat && (
              <span>♥ {lastHeartbeat.toLocaleTimeString()}</span>
            )}
            {lastUpdated && (
              <span>Updated {lastUpdated.toLocaleTimeString()}</span>
            )}
          </div>
        </div>
      </header>

      {/* ── Main grid ── */}
      <main className="max-w-[1600px] mx-auto px-4 py-4 space-y-4">

        {/* Row 1: 4 stat cards + status bar */}
        <BalanceCard
          balance={balance}
          pnlToday={pnlToday}
          risk={risk}
          lastUpdated={lastUpdated}
        />

        {/* Row 2: Open Positions (left 2/3) + Risk Status (right 1/3) */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 min-h-[280px]">
            <OpenPositions
              positions={positions}
              mode={mode}
              onExitSuccess={onExitSuccess}
            />
          </div>
          <div className="min-h-[280px]">
            <RiskStatus risk={risk} />
          </div>
        </div>

        {/* Row 3: Equity Curve (left 1/2) + City Forecasts (right 1/2) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="min-h-[260px]">
            <EquityCurve history={pnlHistory} />
          </div>
          <div className="min-h-[260px]">
            <CityForecasts scanner={scanner} />
          </div>
        </div>

        {/* Row 4: Opportunity Scanner (left 1/2) + Recent Trades (right 1/2) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="min-h-[300px]">
            <OpportunityScanner scanner={scanner} wsConnected={wsStatus === 'connected'} />
          </div>
          <div className="min-h-[300px]">
            <RecentTrades trades={trades} />
          </div>
        </div>

      </main>

      {/* ── Footer ── */}
      <footer className="border-t border-bg-border mt-8 py-3 text-center text-xs text-text-muted font-mono">
        Kalshi Edge Trader Dashboard — {new Date().getFullYear()}
      </footer>
    </div>
  );
}
