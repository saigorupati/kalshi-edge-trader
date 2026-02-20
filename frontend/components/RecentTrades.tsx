'use client';

import { Trade } from '@/lib/api';
import { clsx } from 'clsx';
import { format, parseISO } from 'date-fns';

interface Props {
  trades: Trade[];
}

export default function RecentTrades({ trades }: Props) {
  const sorted = [...trades].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  ).slice(0, 50);

  return (
    <div className="card h-full flex flex-col">
      <div className="card-header">
        <span className="card-title">Recent Trades</span>
        <span className="badge badge-cyan">{sorted.length}</span>
      </div>

      <div className="flex-1 overflow-auto">
        {sorted.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-text-muted text-sm font-mono">
            No trades yet
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>City</th>
                <th>Qty</th>
                <th className="text-right">Price</th>
                <th className="text-right">Edge</th>
                <th className="text-right">P&L</th>
                <th className="text-center">Result</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((t) => {
                const time = (() => {
                  try { return format(parseISO(t.timestamp), 'MM/dd HH:mm'); }
                  catch { return t.timestamp.slice(0, 16); }
                })();

                const resolved = t.resolved;
                const won = t.resolved_yes;
                const lost = resolved && !won;

                return (
                  <tr key={t.trade_id} className="animate-fade-in">
                    <td className="text-text-muted text-xs">{time}</td>
                    <td>
                      <span className="badge badge-cyan">{t.city}</span>
                    </td>
                    <td className="font-mono">{t.count}</td>
                    <td className="text-right font-mono">
                      ${(t.price_cents / 100).toFixed(2)}
                    </td>
                    <td
                      className={clsx(
                        'text-right font-mono',
                        t.edge >= 0.05 ? 'text-accent-green' : 'text-accent-yellow'
                      )}
                    >
                      {(t.edge * 100).toFixed(1)}%
                    </td>
                    <td
                      className={clsx(
                        'text-right font-mono font-semibold',
                        !resolved
                          ? 'text-text-muted'
                          : (t.pnl ?? 0) >= 0
                          ? 'text-accent-green'
                          : 'text-accent-red'
                      )}
                    >
                      {resolved && t.pnl !== undefined
                        ? `${t.pnl >= 0 ? '+' : ''}$${t.pnl.toFixed(2)}`
                        : '—'}
                    </td>
                    <td className="text-center">
                      {!resolved ? (
                        <span className="badge badge-yellow">OPEN</span>
                      ) : won ? (
                        <span className="badge badge-green">WIN</span>
                      ) : lost ? (
                        <span className="badge badge-red">LOSS</span>
                      ) : (
                        <span className="badge badge-cyan">EXIT</span>
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
  );
}
