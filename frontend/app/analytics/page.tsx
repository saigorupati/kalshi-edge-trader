'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import Link from 'next/link';
import { api, Trade, CalibrationRecord, PnLRecord, PnLToday } from '@/lib/api';
import TradeHistory    from '@/components/analytics/TradeHistory';
import ModelAccuracy   from '@/components/analytics/ModelAccuracy';
import PerformanceStats from '@/components/analytics/PerformanceStats';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
} from 'recharts';
import { format, parseISO } from 'date-fns';

// ── Tab type ────────────────────────────────────────────────────────────────

type Tab = 'performance' | 'trades' | 'model';

const TABS: { id: Tab; label: string }[] = [
  { id: 'performance', label: 'Performance' },
  { id: 'trades',      label: 'Trade History' },
  { id: 'model',       label: 'Model Accuracy' },
];

// ── Daily P&L bar chart ─────────────────────────────────────────────────────

interface DailyBarPoint { date: string; pnl: number }

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function DailyPnLTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const v: number = payload[0].value;
  return (
    <div className="bg-bg-card border border-bg-border rounded px-3 py-2 text-xs font-mono shadow-xl">
      <p className="text-text-secondary mb-1">{label}</p>
      <p className={v >= 0 ? 'text-accent-green' : 'text-accent-red'}>
        P&L: {v >= 0 ? '+' : ''}${v.toFixed(2)}
      </p>
    </div>
  );
}

function DailyPnLBar({ history, pnlToday }: { history: PnLRecord[]; pnlToday: PnLToday | null }) {
  const todayStr = new Date().toISOString().slice(0, 10);

  // Build chart data from EOD snapshots, then overlay today's live P&L
  const snapshotMap = new Map(history.map((r) => [r.date, r.realized_pnl]));

  // Always override/inject today's bar with live data (more up-to-date than EOD snapshot)
  if (pnlToday) {
    snapshotMap.set(todayStr, pnlToday.realized_pnl);
  }

  const data: DailyBarPoint[] = [...snapshotMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, pnl]) => ({
      date: (() => { try { return format(parseISO(date), 'MMM d'); } catch { return date; } })(),
      pnl,
    }));

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Daily Realized P&L</span>
        <span className="text-text-muted text-xs font-mono">{data.length} days</span>
      </div>
      <div className="p-4 min-h-[180px]">
        {data.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-text-muted text-sm font-mono">
            No daily snapshots yet
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e1e30" />
              <XAxis dataKey="date" tick={{ fill: '#555577', fontSize: 10, fontFamily: 'JetBrains Mono' }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: '#555577', fontSize: 10, fontFamily: 'JetBrains Mono' }} axisLine={false} tickLine={false} tickFormatter={(v) => `$${v}`} width={50} />
              <Tooltip content={<DailyPnLTooltip />} />
              <Bar dataKey="pnl" name="Daily P&L" radius={[3, 3, 0, 0]}>
                {data.map((d, i) => (
                  <Cell key={i} fill={d.pnl >= 0 ? '#00ff88' : '#ff3366'} opacity={0.85} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

interface PageState {
  trades:      Trade[];
  calibration: CalibrationRecord[];
  history:     PnLRecord[];
  pnlToday:    PnLToday | null;
  loading:     boolean;
  error:       string | null;
  lastUpdated: Date | null;
}

const LOOKBACK_OPTIONS = [
  { label: '7d',  days: 7  },
  { label: '14d', days: 14 },
  { label: '30d', days: 30 },
  { label: '60d', days: 60 },
  { label: '90d', days: 90 },
];

export default function AnalyticsPage() {
  const [tab,      setTab]      = useState<Tab>('performance');
  const [lookback, setLookback] = useState(30);
  const [state,    setState]    = useState<PageState>({
    trades: [], calibration: [], history: [], pnlToday: null, loading: true, error: null, lastUpdated: null,
  });

  const mountedRef = useRef(true);

  function patch(partial: Partial<PageState>) {
    if (mountedRef.current) setState((prev) => ({ ...prev, ...partial }));
  }

  const fetchData = useCallback(async (days: number) => {
    patch({ loading: true, error: null });
    try {
      const [trades, calibration, history, pnlToday] = await Promise.all([
        api.tradesRange(days),
        api.calibrationAll(days),
        api.pnlHistory(),
        api.pnlToday(),
      ]);
      patch({ trades, calibration, history, pnlToday, loading: false, lastUpdated: new Date() });
    } catch (e: unknown) {
      patch({ loading: false, error: e instanceof Error ? e.message : 'Failed to load analytics' });
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    fetchData(lookback);
    return () => { mountedRef.current = false; };
  }, [fetchData, lookback]);

  const { trades, calibration, history, pnlToday, loading, error, lastUpdated } = state;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-bg-primary text-text-primary">

      {/* Header */}
      <header className="border-b border-bg-border bg-bg-secondary sticky top-0 z-40">
        <div className="max-w-[1600px] mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link href="/dashboard" className="text-text-muted text-xs font-mono hover:text-accent-cyan transition-colors">
              ← Dashboard
            </Link>
            <span className="text-bg-border">|</span>
            <span className="text-accent-cyan font-mono font-bold tracking-wider text-base">
              ANALYTICS
            </span>
          </div>
          <div className="flex items-center gap-3">
            {/* Lookback selector */}
            <div className="flex items-center gap-1 text-xs font-mono">
              <span className="text-text-muted mr-1">Lookback:</span>
              {LOOKBACK_OPTIONS.map((o) => (
                <button
                  key={o.days}
                  onClick={() => setLookback(o.days)}
                  className={`px-2 py-1 rounded border transition-colors ${
                    lookback === o.days
                      ? 'bg-cyan-900/40 border-accent-cyan/40 text-accent-cyan'
                      : 'border-bg-border text-text-muted hover:text-text-primary hover:border-bg-border'
                  }`}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <button
              onClick={() => fetchData(lookback)}
              className="btn-ghost text-xs px-2 py-1"
              disabled={loading}
            >
              {loading ? '↻' : '↺'} Refresh
            </button>
            {lastUpdated && (
              <span className="text-text-muted text-xs font-mono">
                {lastUpdated.toLocaleTimeString()}
              </span>
            )}
          </div>
        </div>

        {/* Tab bar */}
        <div className="max-w-[1600px] mx-auto px-4 flex gap-1 border-t border-bg-border">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-4 py-2 text-xs font-mono font-semibold tracking-wide transition-colors border-b-2 ${
                tab === t.id
                  ? 'border-accent-cyan text-accent-cyan'
                  : 'border-transparent text-text-muted hover:text-text-primary'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </header>

      {/* Body */}
      <main className="max-w-[1600px] mx-auto px-4 py-4 space-y-4">

        {loading && (
          <div className="flex items-center justify-center py-20">
            <div className="text-center space-y-3">
              <div className="dot-yellow w-4 h-4 mx-auto animate-pulse" />
              <p className="text-text-secondary font-mono text-sm">Loading {lookback}d of data…</p>
            </div>
          </div>
        )}

        {error && !loading && (
          <div className="card p-6 text-center">
            <p className="text-accent-red font-mono text-sm">{error}</p>
            <button className="btn-cyan mt-3" onClick={() => fetchData(lookback)}>Retry</button>
          </div>
        )}

        {!loading && !error && (
          <>
            {/* Always show daily P&L bar at top */}
            <DailyPnLBar history={history} pnlToday={pnlToday} />

            {/* Tab content */}
            {tab === 'performance' && (
              <PerformanceStats trades={trades} />
            )}

            {tab === 'trades' && (
              <TradeHistory trades={trades} />
            )}

            {tab === 'model' && (
              <ModelAccuracy trades={trades} calibration={calibration} />
            )}
          </>
        )}
      </main>

      <footer className="border-t border-bg-border mt-8 py-3 text-center text-xs text-text-muted font-mono">
        Kalshi Edge Trader — Analytics — {new Date().getFullYear()}
      </footer>
    </div>
  );
}
