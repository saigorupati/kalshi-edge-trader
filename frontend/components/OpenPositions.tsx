'use client';

import { useState } from 'react';
import { OpenPosition, api, LimitSellResult } from '@/lib/api';
import { clsx } from 'clsx';

/**
 * Convert a raw Kalshi ticker like "KXHIGHCHI-26FEB26-T35" into a
 * human-readable label like "CHI 35-36° · Feb 26".
 *
 * Bucket floors by series:
 *   CHI / NY / MIA / LAX:  34, 35, 37, 39, 41, 43  (lowest=34, highest=43)
 *   PHX:                   61, 63, 65, 67, 69, 71  (lowest=61, highest=71)
 */
const SERIES_BUCKETS: Record<string, number[]> = {
  KXHIGHCHI:  [34, 35, 37, 39, 41, 43],
  KXHIGHNY:   [34, 35, 37, 39, 41, 43],
  KXHIGHMIA:  [34, 35, 37, 39, 41, 43],
  KXHIGHLAX:  [34, 35, 37, 39, 41, 43],
  KXHIGHTPHX: [61, 63, 65, 67, 69, 71],
};

const CITY_MAP: Record<string, string> = {
  KXHIGHCHI:  'CHI',
  KXHIGHNY:   'NYC',
  KXHIGHMIA:  'MIA',
  KXHIGHLAX:  'LA',
  KXHIGHTPHX: 'PHX',
};

/** "26FEB21" → "Feb 21" — Kalshi format is YRMONDD (year=26, month=FEB, day=21) */
function parseDatePart(raw: string): string {
  const m = raw.match(/^(\d{2})([A-Z]{3})(\d{2})$/i);
  if (!m) return raw;
  // m[1] = 2-digit year, m[2] = month abbrev, m[3] = day
  const day   = parseInt(m[3], 10);
  const month = m[2].charAt(0).toUpperCase() + m[2].slice(1).toLowerCase();
  return `${month} ${day}`;
}

function friendlyTicker(ticker: string): string {
  // e.g. "KXHIGHCHI-26FEB26-T35"
  const parts = ticker.split('-');
  if (parts.length < 3) return ticker;

  const series   = parts[0];                      // "KXHIGHCHI"
  const datePart = parts[parts.length - 2];       // "26FEB26"
  const suffix   = parts[parts.length - 1];       // "T35"
  const match    = suffix.match(/^T(\d+)$/i);
  if (!match) return ticker;

  const n       = parseInt(match[1], 10);
  const buckets = SERIES_BUCKETS[series];
  const city    = CITY_MAP[series] ?? series.replace('KXHIGH', '').replace(/^T/, '');
  const date    = parseDatePart(datePart);

  let range: string;
  if (buckets) {
    if (n === buckets[0])                       range = `≤${n}°`;
    else if (n === buckets[buckets.length - 1]) range = `≥${n}°`;
    else                                        range = `${n}-${n + 1}°`;
  } else {
    range = `${n}-${n + 1}°`;
  }

  return `${city} ${range} · ${date}`;
}

interface Props {
  positions: OpenPosition[];
  mode: string;
  onExitSuccess: () => void;
}

type SellTab = 'quick' | 'limit';

function SellModal({
  position,
  onSuccess,
  onCancel,
}: {
  position: OpenPosition;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const [tab, setTab]           = useState<SellTab>('quick');
  const [limitPrice, setLimit]  = useState<string>('');
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [result, setResult]     = useState<LimitSellResult | null>(null);

  const entryPrice  = position.price_cents / 100;
  const limitCents  = Math.round(parseFloat(limitPrice) * 100);
  const limitValid  = !isNaN(limitCents) && limitCents >= 1 && limitCents <= 99;
  const limitPnl    = limitValid ? (limitCents / 100 - entryPrice) * position.count : null;

  // Quick sell: cancel the resting order (marks resolved, P&L = 0)
  async function handleQuickSell() {
    if (!position.order_id) {
      setError('No order ID available — cannot cancel.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.cancelOrder(position.order_id, position.trade_id);
      onSuccess();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Quick sell failed');
      setLoading(false);
    }
  }

  // Limit sell: place a limit sell order at the specified price
  async function handleLimitSell() {
    if (!limitValid) {
      setError('Enter a valid price between $0.01 and $0.99');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await api.limitSell(position.ticker, position.trade_id, limitCents);
      setResult(res);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Limit sell failed');
      setLoading(false);
    }
  }

  // ── Success screen ────────────────────────────────────────────────
  if (result) {
    const pnl = result.pnl;
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
        <div className="card w-full max-w-sm mx-4 shadow-2xl border-accent-green/30">
          <div className="card-header border-accent-green/20">
            <span className="card-title text-accent-green">
              {result.status === 'simulated_fill' ? 'Simulated Fill' : 'Order Placed'}
            </span>
          </div>
          <div className="p-4 space-y-3 text-sm font-mono">
            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-text-secondary">
              <span>Ticker</span>
              <span className="text-text-primary truncate">{result.ticker}</span>
              <span>Contracts</span>
              <span className="text-text-primary">{result.count}</span>
              <span>Sell Price</span>
              <span className="text-text-primary">${(result.sell_price_cents / 100).toFixed(2)}</span>
              <span>Est. P&L</span>
              <span className={pnl >= 0 ? 'text-accent-green' : 'text-accent-red'}>
                {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
              </span>
              {result.order_id && (
                <>
                  <span>Order ID</span>
                  <span className="text-text-muted text-xs truncate">{result.order_id}</span>
                </>
              )}
            </div>
            {result.status === 'resting' && (
              <p className="text-text-muted text-xs border-t border-bg-border pt-3">
                Limit sell is resting on Kalshi. It will fill when the market reaches your price.
              </p>
            )}
          </div>
          <div className="p-4 pt-0">
            <button className="btn-cyan w-full" onClick={() => { onSuccess(); }}>
              Done
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Main modal ────────────────────────────────────────────────────
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="card w-full max-w-sm mx-4 shadow-2xl">
        {/* Header */}
        <div className="card-header">
          <span className="card-title">Close Position</span>
          <button
            className="text-text-muted hover:text-text-primary text-lg leading-none px-1"
            onClick={onCancel}
            disabled={loading}
          >
            ×
          </button>
        </div>

        {/* Position summary */}
        <div className="px-4 pt-3 pb-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs font-mono text-text-secondary">
          <span>Ticker</span>
          <span className="text-text-primary truncate" title={position.ticker}>
            {friendlyTicker(position.ticker)}
          </span>
          <span>Contracts</span>
          <span className="text-text-primary">{position.count}</span>
          <span>Entry</span>
          <span className="text-text-primary">${entryPrice.toFixed(2)}</span>
          {position.unrealized_pnl != null && (
            <>
              <span>Unreal. P&L</span>
              <span className={position.unrealized_pnl >= 0 ? 'text-accent-green' : 'text-accent-red'}>
                {position.unrealized_pnl >= 0 ? '+' : ''}${position.unrealized_pnl.toFixed(2)}
              </span>
            </>
          )}
        </div>

        {/* Tabs */}
        <div className="flex border-b border-bg-border mx-4 mt-1">
          {(['quick', 'limit'] as SellTab[]).map((t) => (
            <button
              key={t}
              disabled={loading}
              onClick={() => { setTab(t); setError(null); }}
              className={clsx(
                'flex-1 py-2 text-xs font-mono font-semibold uppercase tracking-wider transition-colors',
                tab === t
                  ? 'text-accent-cyan border-b-2 border-accent-cyan'
                  : 'text-text-muted hover:text-text-secondary'
              )}
            >
              {t === 'quick' ? '⚡ Quick Sell' : '📉 Limit Sell'}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="p-4 space-y-3">
          {tab === 'quick' ? (
            <>
              <p className="text-xs text-text-secondary font-mono leading-relaxed">
                Cancels the resting Kalshi order immediately and marks the trade closed.
                {' '}P&L is recorded as <span className="text-accent-yellow">$0.00</span> — use Limit Sell
                to capture a partial profit instead.
              </p>
              {error && (
                <p className="text-xs text-accent-red font-mono">{error}</p>
              )}
              <div className="flex gap-2 pt-1">
                <button className="btn-ghost flex-1" onClick={onCancel} disabled={loading}>
                  Cancel
                </button>
                <button className="btn-red flex-1" onClick={handleQuickSell} disabled={loading}>
                  {loading ? 'Exiting…' : 'Confirm Exit'}
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="text-xs text-text-secondary font-mono leading-relaxed">
                Places a limit sell order on Kalshi at your specified price.
                In paper mode this simulates an instant fill and records P&L.
              </p>
              <div className="space-y-1">
                <label className="text-xs text-text-muted font-mono">Limit Price ($)</label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min="0.01"
                    max="0.99"
                    step="0.01"
                    placeholder="0.25"
                    value={limitPrice}
                    onChange={(e) => setLimit(e.target.value)}
                    disabled={loading}
                    className={clsx(
                      'flex-1 bg-bg-primary border rounded px-3 py-1.5 text-sm font-mono',
                      'text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-cyan',
                      limitPrice && !limitValid
                        ? 'border-accent-red'
                        : 'border-bg-border'
                    )}
                  />
                  <span className="text-xs text-text-muted font-mono">/ $1.00</span>
                </div>
                {limitPnl !== null && (
                  <p className={clsx(
                    'text-xs font-mono',
                    limitPnl >= 0 ? 'text-accent-green' : 'text-accent-red'
                  )}>
                    Est. P&L: {limitPnl >= 0 ? '+' : ''}${limitPnl.toFixed(2)}
                    {' '}({limitPnl >= 0 ? '+' : ''}{(((limitCents / 100) / entryPrice - 1) * 100).toFixed(1)}%)
                  </p>
                )}
              </div>
              {error && (
                <p className="text-xs text-accent-red font-mono">{error}</p>
              )}
              <div className="flex gap-2 pt-1">
                <button className="btn-ghost flex-1" onClick={onCancel} disabled={loading}>
                  Cancel
                </button>
                <button
                  className="btn-cyan flex-1"
                  onClick={handleLimitSell}
                  disabled={loading || !limitValid}
                >
                  {loading ? 'Placing…' : 'Place Limit Sell'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function OpenPositions({ positions, mode, onExitSuccess }: Props) {
  const [confirm, setConfirm] = useState<OpenPosition | null>(null);
  const [error, setError]     = useState<string | null>(null);

  const isPaper = mode === 'paper';

  return (
    <>
      {confirm && (
        <SellModal
          position={confirm}
          onSuccess={() => { setConfirm(null); onExitSuccess(); }}
          onCancel={() => setConfirm(null)}
        />
      )}

      <div className="card h-full flex flex-col">
        <div className="card-header">
          <span className="card-title">Open Positions</span>
          <span
            className={clsx(
              'badge',
              positions.length > 0 ? 'badge-cyan' : 'badge-yellow'
            )}
          >
            {positions.length}
          </span>
        </div>

        {error && (
          <div className="mx-4 mt-3 px-3 py-2 bg-red-950/40 border border-accent-red/30 rounded text-xs text-accent-red font-mono">
            {error}
          </div>
        )}

        <div className="flex-1 overflow-auto">
          {positions.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-text-muted text-sm font-mono">
              No open positions
            </div>
          ) : (
            <table className="data-table w-full">
              <thead>
                <tr>
                  <th>Market</th>
                  <th className="!text-right">Qty</th>
                  <th className="!text-right">Entry$</th>
                  <th className="!text-right">Last$</th>
                  <th className="!text-right">Model%</th>
                  <th className="!text-right">Edge</th>
                  <th className="!text-right" style={{whiteSpace:'nowrap'}}>Unreal P&L</th>
                  <th className="!text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const unreal     = p.unrealized_pnl ?? null;
                  const mktPrice   = p.current_price ?? null;
                  const entryPrice = p.price_cents / 100;
                  const mktDiff    = mktPrice !== null ? mktPrice - entryPrice : null;
                  return (
                    <tr key={p.trade_id} className="animate-fade-in">
                      <td className="font-mono text-sm" title={p.ticker}>
                        {friendlyTicker(p.ticker)}
                      </td>
                      <td className="text-right">{p.count}</td>
                      <td className="text-right">${entryPrice.toFixed(2)}</td>
                      <td className={clsx(
                        'text-right',
                        mktDiff === null ? '!text-text-muted'
                          : mktDiff > 0 ? '!text-accent-green'
                          : mktDiff < 0 ? '!text-accent-red'
                          : ''
                      )}>
                        {mktPrice !== null ? `$${mktPrice.toFixed(2)}` : '—'}
                      </td>
                      <td className="text-right !text-accent-purple">
                        {(p.model_prob * 100).toFixed(1)}%
                      </td>
                      <td className={clsx(
                        'text-right',
                        p.edge >= 0.05 ? '!text-accent-green' : '!text-accent-yellow'
                      )}>
                        {(p.edge * 100).toFixed(1)}%
                      </td>
                      <td className={clsx(
                        'text-right font-semibold',
                        unreal === null  ? '!text-text-muted'
                          : unreal >= 0 ? '!text-accent-green'
                          : '!text-accent-red'
                      )}>
                        {unreal !== null
                          ? `${unreal >= 0 ? '+' : ''}$${unreal.toFixed(2)}`
                          : '—'}
                      </td>
                      <td className="text-right">
                        <button
                          className="btn-red text-[11px]"
                          disabled={!isPaper && !p.order_id}
                          onClick={() => { setError(null); setConfirm(p); }}
                        >
                          CLOSE
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}
