'use client';

import { useMemo } from 'react';
import { Trade } from '@/lib/api';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
  PieChart,
  Pie,
  Legend,
} from 'recharts';

interface Props {
  trades: Trade[];
}

const CITY_COLORS: Record<string, string> = {
  NYC: '#00d4ff',
  LA:  '#00ff88',
  CHI: '#ff8c00',
  PHX: '#ff3366',
  MIA: '#9b59ff',
};

function pct(v: number, total: number) {
  return total > 0 ? ((v / total) * 100).toFixed(1) : '—';
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function PnLTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-bg-card border border-bg-border rounded px-3 py-2 text-xs font-mono shadow-xl">
      <p className="text-text-secondary mb-1">{label}</p>
      {payload.map((p: { name: string; value: number; color: string }) => (
        <p key={p.name} style={{ color: p.color }}>
          {p.name}: {p.value >= 0 ? '+' : ''}${p.value.toFixed(2)}
        </p>
      ))}
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function WinLossTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const { name, value, payload: entry } = payload[0];
  return (
    <div className="bg-bg-card border border-bg-border rounded px-3 py-2 text-xs font-mono shadow-xl">
      <p style={{ color: entry.fill }}>{name}: {value} ({entry.pct}%)</p>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function CountTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-bg-card border border-bg-border rounded px-3 py-2 text-xs font-mono shadow-xl">
      <p className="text-text-secondary mb-1">Edge bucket: {label}</p>
      {payload.map((p: { name: string; value: number; color: string }) => (
        <p key={p.name} style={{ color: p.color }}>{p.name}: {p.value}</p>
      ))}
    </div>
  );
}

export default function PerformanceStats({ trades }: Props) {
  const resolved = useMemo(() => trades.filter((t) => t.resolved), [trades]);

  // ── City breakdown ──────────────────────────────────────────────────────────
  const cityStats = useMemo(() => {
    const cities: Record<string, { wins: number; losses: number; pnl: number; count: number; totalRisk: number }> = {};
    for (const t of resolved) {
      const c = t.city ?? 'Unknown';
      if (!cities[c]) cities[c] = { wins: 0, losses: 0, pnl: 0, count: 0, totalRisk: 0 };
      cities[c].count  += 1;
      cities[c].pnl    += t.pnl ?? 0;
      cities[c].totalRisk += t.dollar_risk ?? 0;
      if (t.resolved_yes) cities[c].wins  += 1;
      else                cities[c].losses += 1;
    }
    return Object.entries(cities)
      .map(([city, s]) => ({
        city,
        wins:    s.wins,
        losses:  s.losses,
        count:   s.count,
        pnl:     s.pnl,
        winRate: s.count > 0 ? (s.wins / s.count) * 100 : 0,
        roi:     s.totalRisk > 0 ? (s.pnl / s.totalRisk) * 100 : 0,
      }))
      .sort((a, b) => b.pnl - a.pnl);
  }, [resolved]);

  // ── Edge bucket breakdown ───────────────────────────────────────────────────
  const edgeBuckets = useMemo(() => {
    const EDGES = [
      { label: '<0%',  min: -Infinity, max: 0    },
      { label: '0–2%', min: 0,         max: 0.02 },
      { label: '2–5%', min: 0.02,      max: 0.05 },
      { label: '5–10%',min: 0.05,      max: 0.10 },
      { label: '10–15%',min: 0.10,     max: 0.15 },
      { label: '>15%', min: 0.15,      max: Infinity },
    ];
    return EDGES.map(({ label, min, max }) => {
      const bucket = resolved.filter((t) => t.edge >= min && t.edge < max);
      const wins   = bucket.filter((t) => t.resolved_yes).length;
      const losses = bucket.length - wins;
      const pnl    = bucket.reduce((s, t) => s + (t.pnl ?? 0), 0);
      return { label, wins, losses, pnl, count: bucket.length };
    }).filter((b) => b.count > 0);
  }, [resolved]);

  // ── Strategy breakdown ──────────────────────────────────────────────────────
  const strategyStats = useMemo(() => {
    const groups: Record<string, { wins: number; count: number; pnl: number }> = {};
    for (const t of resolved) {
      const s = t.strategy ?? 'single';
      if (!groups[s]) groups[s] = { wins: 0, count: 0, pnl: 0 };
      groups[s].count += 1;
      groups[s].pnl   += t.pnl ?? 0;
      if (t.resolved_yes) groups[s].wins += 1;
    }
    return Object.entries(groups).map(([name, v]) => ({
      name,
      wins:    v.wins,
      losses:  v.count - v.wins,
      count:   v.count,
      pnl:     v.pnl,
      winRate: v.count > 0 ? (v.wins / v.count) * 100 : 0,
    }));
  }, [resolved]);

  // ── Win/Loss pie ────────────────────────────────────────────────────────────
  const totalWins   = resolved.filter((t) => t.resolved_yes).length;
  const totalLosses = resolved.length - totalWins;
  const pieData = [
    { name: 'Wins',   value: totalWins,   fill: '#00ff88', pct: pct(totalWins,   resolved.length) },
    { name: 'Losses', value: totalLosses, fill: '#ff3366', pct: pct(totalLosses, resolved.length) },
  ].filter((d) => d.value > 0);

  const totalPnl = resolved.reduce((s, t) => s + (t.pnl ?? 0), 0);
  const avgPnlPerTrade = resolved.length > 0 ? totalPnl / resolved.length : 0;
  const avgWinSize  = resolved.filter((t) => t.resolved_yes && (t.pnl ?? 0) > 0).reduce((s, t, _, a) => s + (t.pnl ?? 0) / a.length, 0);
  const avgLossSize = resolved.filter((t) => !t.resolved_yes && (t.pnl ?? 0) < 0).reduce((s, t, _, a) => s + Math.abs(t.pnl ?? 0) / a.length, 0);

  return (
    <div className="space-y-4">

      {/* ── Summary KPIs ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          {
            label: 'Total P&L',
            value: `${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)}`,
            color: totalPnl >= 0 ? 'text-accent-green' : 'text-accent-red',
            sub: `${resolved.length} resolved trades`,
          },
          {
            label: 'Win Rate',
            value: resolved.length > 0 ? `${pct(totalWins, resolved.length)}%` : '—',
            color: totalWins / (resolved.length || 1) >= 0.5 ? 'text-accent-green' : 'text-accent-red',
            sub: `${totalWins}W / ${totalLosses}L`,
          },
          {
            label: 'Avg P&L / Trade',
            value: resolved.length > 0 ? `${avgPnlPerTrade >= 0 ? '+' : ''}$${avgPnlPerTrade.toFixed(2)}` : '—',
            color: avgPnlPerTrade >= 0 ? 'text-accent-green' : 'text-accent-red',
            sub: 'resolved trades only',
          },
          {
            label: 'Win/Loss Ratio',
            value: avgLossSize > 0 ? `${(avgWinSize / avgLossSize).toFixed(2)}x` : '—',
            color: avgWinSize > avgLossSize ? 'text-accent-green' : 'text-accent-yellow',
            sub: `avg win $${avgWinSize.toFixed(2)} / avg loss $${avgLossSize.toFixed(2)}`,
          },
        ].map((s) => (
          <div key={s.label} className="card p-4">
            <p className="text-[10px] font-mono uppercase tracking-widest text-text-muted mb-1">{s.label}</p>
            <p className={`text-2xl font-bold font-mono ${s.color}`}>{s.value}</p>
            <p className="text-[10px] text-text-muted mt-1">{s.sub}</p>
          </div>
        ))}
      </div>

      {/* ── Charts row ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

        {/* Win/Loss Pie */}
        <div className="card flex flex-col">
          <div className="card-header">
            <span className="card-title">Win / Loss Split</span>
          </div>
          <div className="flex-1 p-4 min-h-[220px] flex items-center justify-center">
            {pieData.length === 0 ? (
              <p className="text-text-muted text-sm font-mono">No resolved trades</p>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie data={pieData} dataKey="value" cx="50%" cy="50%" outerRadius={70} paddingAngle={3}>
                    {pieData.map((entry, i) => (
                      <Cell key={i} fill={entry.fill} opacity={0.85} />
                    ))}
                  </Pie>
                  <Tooltip content={<WinLossTooltip />} />
                  <Legend
                    formatter={(value: string) => <span style={{ color: '#8888aa', fontSize: 11 }}>{value}</span>}
                  />
                </PieChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* City P&L bar */}
        <div className="card flex flex-col lg:col-span-2">
          <div className="card-header">
            <span className="card-title">P&L by City</span>
            <span className="text-text-muted text-xs font-mono">resolved trades</span>
          </div>
          <div className="flex-1 p-4 min-h-[220px]">
            {cityStats.length === 0 ? (
              <div className="flex items-center justify-center h-full text-text-muted text-sm font-mono">No data</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={cityStats} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e30" />
                  <XAxis dataKey="city" tick={{ fill: '#555577', fontSize: 11, fontFamily: 'JetBrains Mono' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: '#555577', fontSize: 10, fontFamily: 'JetBrains Mono' }} axisLine={false} tickLine={false} tickFormatter={(v) => `$${v}`} width={50} />
                  <Tooltip content={<PnLTooltip />} />
                  <Bar dataKey="pnl" name="P&L" radius={[3, 3, 0, 0]}>
                    {cityStats.map((entry) => (
                      <Cell key={entry.city} fill={entry.pnl >= 0 ? '#00ff88' : '#ff3366'} opacity={0.85} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>

      {/* ── Edge bucket chart + strategy ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* Trades by edge bucket */}
        <div className="card flex flex-col">
          <div className="card-header">
            <span className="card-title">Wins & Losses by Edge Bucket</span>
          </div>
          <div className="flex-1 p-4 min-h-[220px]">
            {edgeBuckets.length === 0 ? (
              <div className="flex items-center justify-center h-full text-text-muted text-sm font-mono">No data</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={edgeBuckets} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e30" />
                  <XAxis dataKey="label" tick={{ fill: '#555577', fontSize: 10, fontFamily: 'JetBrains Mono' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: '#555577', fontSize: 10, fontFamily: 'JetBrains Mono' }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip content={<CountTooltip />} />
                  <Bar dataKey="wins"   name="Wins"   fill="#00ff88" opacity={0.85} radius={[2, 2, 0, 0]} stackId="a" />
                  <Bar dataKey="losses" name="Losses" fill="#ff3366" opacity={0.85} radius={[0, 0, 0, 0]} stackId="a" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* City table */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">City Breakdown</span>
          </div>
          <div className="overflow-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>City</th>
                  <th className="text-right">Trades</th>
                  <th className="text-right">Win%</th>
                  <th className="text-right">P&L</th>
                  <th className="text-right">ROI</th>
                </tr>
              </thead>
              <tbody>
                {cityStats.length === 0 ? (
                  <tr><td colSpan={5} className="text-center text-text-muted py-6">No resolved trades</td></tr>
                ) : cityStats.map((row) => (
                  <tr key={row.city}>
                    <td>
                      <span className="badge" style={{
                        background: `${CITY_COLORS[row.city] ?? '#555577'}22`,
                        color:       CITY_COLORS[row.city] ?? '#888',
                        border:     `1px solid ${CITY_COLORS[row.city] ?? '#555577'}44`,
                      }}>{row.city}</span>
                    </td>
                    <td className="text-right font-mono text-text-secondary">{row.count}</td>
                    <td className={`text-right font-mono font-semibold ${row.winRate >= 50 ? 'text-accent-green' : 'text-accent-red'}`}>
                      {row.winRate.toFixed(0)}%
                    </td>
                    <td className={`text-right font-mono font-semibold ${row.pnl >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                      {row.pnl >= 0 ? '+' : ''}${row.pnl.toFixed(2)}
                    </td>
                    <td className={`text-right font-mono ${row.roi >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                      {row.roi >= 0 ? '+' : ''}{row.roi.toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Strategy sub-table */}
          {strategyStats.length > 1 && (
            <>
              <div className="card-header border-t border-bg-border mt-0">
                <span className="card-title">Strategy Breakdown</span>
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th className="text-right">Trades</th>
                    <th className="text-right">Win%</th>
                    <th className="text-right">P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {strategyStats.map((row) => (
                    <tr key={row.name}>
                      <td>
                        <span className={`badge ${row.name === 'bracket' ? 'badge-purple' : 'badge-cyan'}`}>
                          {row.name}
                        </span>
                      </td>
                      <td className="text-right font-mono text-text-secondary">{row.count}</td>
                      <td className={`text-right font-mono ${row.winRate >= 50 ? 'text-accent-green' : 'text-accent-red'}`}>
                        {row.winRate.toFixed(0)}%
                      </td>
                      <td className={`text-right font-mono font-semibold ${row.pnl >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                        {row.pnl >= 0 ? '+' : ''}${row.pnl.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
