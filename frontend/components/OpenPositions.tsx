'use client';

import { useState } from 'react';
import { OpenPosition, api } from '@/lib/api';
import { clsx } from 'clsx';

interface Props {
  positions: OpenPosition[];
  mode: string;
  onExitSuccess: () => void;
}

function ConfirmModal({
  position,
  onConfirm,
  onCancel,
  loading,
}: {
  position: OpenPosition;
  onConfirm: () => void;
  onCancel: () => void;
  loading: boolean;
}) {
  const cost = (position.price_cents / 100) * position.count;
  const current = position.market_yes_bid
    ? (position.market_yes_bid / 100) * position.count
    : null;
  const pnl = current !== null ? current - cost : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="card w-full max-w-sm mx-4 shadow-2xl border-accent-red/30">
        <div className="card-header border-accent-red/20">
          <span className="card-title text-accent-red">Confirm Exit Trade</span>
        </div>
        <div className="p-4 space-y-3 text-sm font-mono">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-text-secondary">
            <span>City</span>
            <span className="text-text-primary font-semibold">{position.city}</span>
            <span>Ticker</span>
            <span className="text-text-primary truncate">{position.ticker}</span>
            <span>Contracts</span>
            <span className="text-text-primary">{position.count}</span>
            <span>Entry Price</span>
            <span className="text-text-primary">${(position.price_cents / 100).toFixed(2)}</span>
            {pnl !== null && (
              <>
                <span>Est. P&L</span>
                <span className={pnl >= 0 ? 'text-accent-green' : 'text-accent-red'}>
                  {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                </span>
              </>
            )}
          </div>
          <p className="text-text-muted text-xs border-t border-bg-border pt-3">
            This will cancel the resting order on Kalshi and mark the trade resolved with P&amp;L = 0.
          </p>
        </div>
        <div className="flex gap-2 p-4 pt-0">
          <button className="btn-ghost flex-1" onClick={onCancel} disabled={loading}>
            Cancel
          </button>
          <button
            className="btn-red flex-1"
            onClick={onConfirm}
            disabled={loading}
          >
            {loading ? 'Exiting…' : 'Exit Trade'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function OpenPositions({ positions, mode, onExitSuccess }: Props) {
  const [exiting, setExiting] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<OpenPosition | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isPaper = mode === 'paper';

  async function handleExit(position: OpenPosition) {
    if (!position.order_id) {
      setError('No order ID available — cannot cancel.');
      return;
    }
    setExiting(position.order_id);
    setError(null);
    try {
      await api.cancelOrder(position.order_id, position.trade_id);
      onExitSuccess();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Exit failed');
    } finally {
      setExiting(null);
      setConfirm(null);
    }
  }

  return (
    <>
      {confirm && (
        <ConfirmModal
          position={confirm}
          loading={exiting === confirm.order_id}
          onConfirm={() => handleExit(confirm)}
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
            <table className="data-table">
              <thead>
                <tr>
                  <th>City</th>
                  <th>Ticker</th>
                  <th className="text-right">Qty</th>
                  <th className="text-right">Avg$</th>
                  <th className="text-right">Model%</th>
                  <th className="text-right">Edge</th>
                  <th className="text-right">Unreal. P&L</th>
                  <th className="text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const isLoading = exiting === p.order_id;
                  const unreal = p.unrealized_pnl ?? null;
                  return (
                    <tr key={p.trade_id} className="animate-fade-in">
                      <td>
                        <span className="badge badge-cyan">{p.city}</span>
                      </td>
                      <td className="text-text-secondary text-xs max-w-[160px] truncate">
                        {p.ticker}
                      </td>
                      <td className="text-right">{p.count}</td>
                      <td className="text-right">
                        ${(p.price_cents / 100).toFixed(2)}
                      </td>
                      <td className="text-right text-accent-purple">
                        {(p.model_prob * 100).toFixed(1)}%
                      </td>
                      <td
                        className={clsx(
                          'text-right',
                          p.edge >= 0.05
                            ? 'text-accent-green'
                            : 'text-accent-yellow'
                        )}
                      >
                        {(p.edge * 100).toFixed(1)}%
                      </td>
                      <td
                        className={clsx(
                          'text-right font-semibold',
                          unreal === null
                            ? 'text-text-muted'
                            : unreal >= 0
                            ? 'text-accent-green'
                            : 'text-accent-red'
                        )}
                      >
                        {unreal !== null
                          ? `${unreal >= 0 ? '+' : ''}$${unreal.toFixed(2)}`
                          : '—'}
                      </td>
                      <td className="text-right">
                        {isPaper ? (
                          <span className="badge badge-yellow">PAPER</span>
                        ) : (
                          <button
                            className="btn-red text-[11px]"
                            disabled={isLoading || !p.order_id}
                            onClick={() => setConfirm(p)}
                          >
                            {isLoading ? '…' : 'EXIT'}
                          </button>
                        )}
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
