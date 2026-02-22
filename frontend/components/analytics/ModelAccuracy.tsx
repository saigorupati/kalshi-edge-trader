'use client';

import { useMemo } from 'react';
import { Trade, CalibrationRecord } from '@/lib/api';
import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  BarChart,
  Bar,
  Cell,
} from 'recharts';

interface Props {
  trades: Trade[];
  calibration: CalibrationRecord[];
}

// ── Scatter: model_prob vs actual outcome (1=YES, 0=NO) ──────────────────────
// Bin model_prob into 10% buckets, compute actual win rate per bucket

interface BucketPoint {
  bucket: string;
  modelAvg: number;
  actualRate: number;
  count: number;
}

function buildCalibrationBuckets(trades: Trade[]): BucketPoint[] {
  const resolved = trades.filter((t) => t.resolved && t.model_prob != null);
  const buckets: Record<number, { sumModel: number; wins: number; total: number }> = {};

  for (const t of resolved) {
    const b = Math.floor(t.model_prob * 10) * 10; // 0,10,20,...,90
    if (!buckets[b]) buckets[b] = { sumModel: 0, wins: 0, total: 0 };
    buckets[b].sumModel += t.model_prob;
    buckets[b].wins += t.resolved_yes ? 1 : 0;
    buckets[b].total += 1;
  }

  return Object.entries(buckets)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([b, v]) => ({
      bucket: `${b}–${Number(b) + 10}%`,
      modelAvg:   Math.round((v.sumModel / v.total) * 1000) / 10,
      actualRate: Math.round((v.wins / v.total) * 1000) / 10,
      count:      v.total,
    }));
}

// ── Scatter: NBM forecast mu vs actual high temperature ──────────────────────
interface ForecastPoint {
  mu: number;
  actual: number;
  city: string;
  date: string;
  error: number;
}

function buildForecastPoints(calibration: CalibrationRecord[]): ForecastPoint[] {
  return calibration
    .filter((r) => r.actual_high != null && r.nbm_mu != null)
    .map((r) => ({
      mu:     r.nbm_mu,
      actual: r.actual_high!,
      city:   r.city,
      date:   r.forecast_date,
      error:  r.actual_high! - r.nbm_mu,
    }));
}

const CITY_COLORS: Record<string, string> = {
  NYC: '#00d4ff',
  LA:  '#00ff88',
  CHI: '#ff8c00',
  PHX: '#ff3366',
  DFW: '#9b59ff',
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function CalibrationTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload as BucketPoint;
  const diff = d.actualRate - d.modelAvg;
  return (
    <div className="bg-bg-card border border-bg-border rounded px-3 py-2 text-xs font-mono shadow-xl">
      <p className="text-text-secondary mb-1">{d.bucket} model prob</p>
      <p className="text-accent-cyan">Model avg: {d.modelAvg.toFixed(1)}%</p>
      <p className={d.actualRate >= d.modelAvg ? 'text-accent-green' : 'text-accent-red'}>
        Actual rate: {d.actualRate.toFixed(1)}%
      </p>
      <p className={Math.abs(diff) < 5 ? 'text-text-muted' : diff > 0 ? 'text-accent-green' : 'text-accent-red'}>
        Δ {diff >= 0 ? '+' : ''}{diff.toFixed(1)}%
      </p>
      <p className="text-text-muted mt-1">n={d.count}</p>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function ForecastTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload as ForecastPoint;
  return (
    <div className="bg-bg-card border border-bg-border rounded px-3 py-2 text-xs font-mono shadow-xl">
      <p className="text-text-secondary mb-1">{d.city} · {d.date}</p>
      <p className="text-accent-cyan">Forecast μ: {d.mu.toFixed(1)}°F</p>
      <p className="text-text-primary">Actual: {d.actual.toFixed(1)}°F</p>
      <p className={Math.abs(d.error) <= 2 ? 'text-accent-green' : 'text-accent-red'}>
        Error: {d.error >= 0 ? '+' : ''}{d.error.toFixed(1)}°F
      </p>
    </div>
  );
}

export default function ModelAccuracy({ trades, calibration }: Props) {
  const buckets       = useMemo(() => buildCalibrationBuckets(trades),   [trades]);
  const forecastPts   = useMemo(() => buildForecastPoints(calibration),  [calibration]);

  // Mean absolute error of forecasts
  const mae = forecastPts.length > 0
    ? forecastPts.reduce((s, p) => s + Math.abs(p.error), 0) / forecastPts.length
    : null;

  // Overall calibration score: avg abs(modelAvg - actualRate) weighted by count
  const totalN = buckets.reduce((s, b) => s + b.count, 0);
  const calScore = totalN > 0
    ? buckets.reduce((s, b) => s + Math.abs(b.modelAvg - b.actualRate) * b.count, 0) / totalN
    : null;

  return (
    <div className="space-y-4">

      {/* ── Row: summary stats ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          {
            label: 'Calibration Error',
            value: calScore != null ? `${calScore.toFixed(1)}%` : '—',
            sub:   'avg |model% − actual%|',
            color: calScore == null ? 'text-text-muted' : calScore < 5 ? 'text-accent-green' : calScore < 10 ? 'text-accent-yellow' : 'text-accent-red',
          },
          {
            label: 'Forecast MAE',
            value: mae != null ? `${mae.toFixed(1)}°F` : '—',
            sub:   'mean absolute temp error',
            color: mae == null ? 'text-text-muted' : mae < 2 ? 'text-accent-green' : mae < 4 ? 'text-accent-yellow' : 'text-accent-red',
          },
          {
            label: 'Calibration Trades',
            value: String(totalN),
            sub:   'resolved with model_prob',
            color: 'text-accent-cyan',
          },
          {
            label: 'Forecast Records',
            value: String(forecastPts.length),
            sub:   'with actual high recorded',
            color: 'text-accent-cyan',
          },
        ].map((s) => (
          <div key={s.label} className="card p-4">
            <p className="text-[10px] font-mono uppercase tracking-widest text-text-muted mb-1">{s.label}</p>
            <p className={`text-2xl font-bold font-mono ${s.color}`}>{s.value}</p>
            <p className="text-[10px] text-text-muted mt-1">{s.sub}</p>
          </div>
        ))}
      </div>

      {/* ── Row: charts side by side ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* Calibration bar: model% vs actual% per bucket */}
        <div className="card flex flex-col">
          <div className="card-header">
            <span className="card-title">Model Calibration</span>
            <span className="text-text-muted text-xs font-mono">model prob vs actual win rate</span>
          </div>
          <div className="flex-1 p-4 min-h-[240px]">
            {buckets.length === 0 ? (
              <div className="flex items-center justify-center h-full text-text-muted text-sm font-mono">
                No resolved trades yet
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={buckets} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e30" />
                  <XAxis
                    dataKey="bucket"
                    tick={{ fill: '#555577', fontSize: 9, fontFamily: 'JetBrains Mono' }}
                    axisLine={false} tickLine={false}
                    interval={0} angle={-30} textAnchor="end" height={40}
                  />
                  <YAxis
                    tick={{ fill: '#555577', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                    axisLine={false} tickLine={false}
                    tickFormatter={(v) => `${v}%`}
                    domain={[0, 100]} width={40}
                  />
                  <Tooltip content={<CalibrationTooltip />} />
                  <Bar dataKey="modelAvg"   name="Model %" fill="#00d4ff" opacity={0.7} radius={[2, 2, 0, 0]} />
                  <Bar dataKey="actualRate" name="Actual %" fill="#00ff88" opacity={0.85} radius={[2, 2, 0, 0]} />
                  {/* Perfect calibration line */}
                  <ReferenceLine y={50} stroke="#555577" strokeDasharray="4 4" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="px-4 pb-3 flex gap-4 text-[10px] font-mono text-text-muted">
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-2 rounded-sm bg-[#00d4ff] opacity-70" />Model %</span>
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-2 rounded-sm bg-[#00ff88] opacity-85" />Actual %</span>
          </div>
        </div>

        {/* Forecast scatter: NBM mu vs actual high */}
        <div className="card flex flex-col">
          <div className="card-header">
            <span className="card-title">Forecast Accuracy</span>
            <span className="text-text-muted text-xs font-mono">NBM μ vs actual high temp</span>
          </div>
          <div className="flex-1 p-4 min-h-[240px]">
            {forecastPts.length === 0 ? (
              <div className="flex items-center justify-center h-full text-text-muted text-sm font-mono">
                No calibration data yet
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e30" />
                  <XAxis
                    type="number" dataKey="mu" name="Forecast μ"
                    tick={{ fill: '#555577', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                    axisLine={false} tickLine={false}
                    tickFormatter={(v) => `${v}°`} label={{ value: 'Forecast μ (°F)', position: 'insideBottom', offset: -2, fill: '#555577', fontSize: 9 }}
                    height={32}
                  />
                  <YAxis
                    type="number" dataKey="actual" name="Actual"
                    tick={{ fill: '#555577', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                    axisLine={false} tickLine={false}
                    tickFormatter={(v) => `${v}°`} width={40}
                  />
                  <Tooltip content={<ForecastTooltip />} />
                  {/* Perfect forecast line y=x */}
                  <ReferenceLine
                    segment={[
                      { x: Math.min(...forecastPts.map((p) => p.mu)),    y: Math.min(...forecastPts.map((p) => p.mu))    },
                      { x: Math.max(...forecastPts.map((p) => p.mu)),    y: Math.max(...forecastPts.map((p) => p.mu))    },
                    ]}
                    stroke="#555577" strokeDasharray="4 4"
                  />
                  {/* One scatter series per city */}
                  {Object.keys(CITY_COLORS).map((city) => {
                    const pts = forecastPts.filter((p) => p.city === city);
                    if (pts.length === 0) return null;
                    return (
                      <Scatter
                        key={city}
                        name={city}
                        data={pts}
                        fill={CITY_COLORS[city]}
                        opacity={0.8}
                        r={4}
                      />
                    );
                  })}
                </ScatterChart>
              </ResponsiveContainer>
            )}
          </div>
          {/* Legend */}
          <div className="px-4 pb-3 flex flex-wrap gap-3 text-[10px] font-mono text-text-muted">
            {Object.entries(CITY_COLORS).map(([city, color]) => (
              <span key={city} className="flex items-center gap-1">
                <span className="inline-block w-2 h-2 rounded-full" style={{ background: color }} />
                {city}
              </span>
            ))}
            <span className="flex items-center gap-1 ml-2">
              <span className="inline-block w-4 border-t border-dashed border-[#555577]" />
              Perfect forecast
            </span>
          </div>
        </div>

      </div>

      {/* ── Bias table per city ── */}
      {calibration.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Forecast Bias by City</span>
            <span className="text-text-muted text-xs font-mono">avg (actual − forecast μ)</span>
          </div>
          <div className="overflow-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>City</th>
                  <th className="text-right">Records</th>
                  <th className="text-right">Avg Forecast μ</th>
                  <th className="text-right">Avg Actual</th>
                  <th className="text-right">Avg Bias</th>
                  <th className="text-right">MAE</th>
                  <th className="text-right">RMSE</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(
                  forecastPts.reduce<Record<string, ForecastPoint[]>>((acc, p) => {
                    if (!acc[p.city]) acc[p.city] = [];
                    acc[p.city].push(p);
                    return acc;
                  }, {})
                )
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([city, pts]) => {
                    const n    = pts.length;
                    const avgMu     = pts.reduce((s, p) => s + p.mu, 0) / n;
                    const avgActual = pts.reduce((s, p) => s + p.actual, 0) / n;
                    const avgBias   = pts.reduce((s, p) => s + p.error, 0) / n;
                    const mae2      = pts.reduce((s, p) => s + Math.abs(p.error), 0) / n;
                    const rmse      = Math.sqrt(pts.reduce((s, p) => s + p.error ** 2, 0) / n);
                    return (
                      <tr key={city}>
                        <td>
                          <span className="badge" style={{ background: `${CITY_COLORS[city]}22`, color: CITY_COLORS[city], border: `1px solid ${CITY_COLORS[city]}44` }}>
                            {city}
                          </span>
                        </td>
                        <td className="text-right font-mono text-text-secondary">{n}</td>
                        <td className="text-right font-mono">{avgMu.toFixed(1)}°F</td>
                        <td className="text-right font-mono">{avgActual.toFixed(1)}°F</td>
                        <td className={`text-right font-mono font-semibold ${Math.abs(avgBias) < 1 ? 'text-accent-green' : Math.abs(avgBias) < 3 ? 'text-accent-yellow' : 'text-accent-red'}`}>
                          {avgBias >= 0 ? '+' : ''}{avgBias.toFixed(2)}°F
                        </td>
                        <td className={`text-right font-mono ${mae2 < 2 ? 'text-accent-green' : mae2 < 4 ? 'text-accent-yellow' : 'text-accent-red'}`}>
                          {mae2.toFixed(2)}°F
                        </td>
                        <td className={`text-right font-mono ${rmse < 2.5 ? 'text-accent-green' : rmse < 5 ? 'text-accent-yellow' : 'text-accent-red'}`}>
                          {rmse.toFixed(2)}°F
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
