'use client';

import { ScannerState, Opportunity } from '@/lib/api';
import { clsx } from 'clsx';

interface Props {
  scanner: ScannerState | null;
  wsConnected: boolean;
}

function EdgeBar({ edge }: { edge: number }) {
  const pct = Math.min(edge * 100 * 3, 100); // scale: 33%+ edge = full bar
  const color = edge >= 0.10 ? '#00ff88' : edge >= 0.05 ? '#ffcc00' : '#ff3366';
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 bg-bg-border rounded-full h-1.5 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span
        className="font-mono text-xs font-semibold w-10"
        style={{ color }}
      >
        {(edge * 100).toFixed(1)}%
      </span>
    </div>
  );
}

function formatRange(opp: Opportunity): string {
  if (opp.is_open_low) return `< ${opp.temp_high}°F`;
  if (opp.is_open_high) return `≥ ${opp.temp_low}°F`;
  return `${opp.temp_low}–${opp.temp_high}°F`;
}

export default function OpportunityScanner({ scanner, wsConnected }: Props) {
  const opps: Opportunity[] = [...(scanner?.opportunities ?? [])].sort(
    (a, b) => b.net_edge - a.net_edge
  );

  return (
    <div className="card h-full flex flex-col">
      <div className="card-header">
        <span className="card-title">Opportunity Scanner</span>
        <div className="flex items-center gap-2">
          {wsConnected ? (
            <>
              <span className="dot-green" />
              <span className="text-xs text-accent-green font-mono">LIVE</span>
            </>
          ) : (
            <>
              <span className="dot-yellow" />
              <span className="text-xs text-accent-yellow font-mono">RECONNECTING</span>
            </>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {!scanner ? (
          <div className="flex flex-col items-center justify-center h-40 gap-2 text-text-muted font-mono text-sm">
            <div className="dot-yellow w-3 h-3" />
            Waiting for first cycle…
          </div>
        ) : opps.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-text-muted text-sm font-mono">
            No edges found this cycle
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>City</th>
                <th>Range</th>
                <th className="text-right">μ / σ</th>
                <th className="text-right">Model%</th>
                <th className="text-right">Ask%</th>
                <th>Net Edge</th>
              </tr>
            </thead>
            <tbody>
              {opps.map((opp, i) => (
                <tr
                  key={`${opp.city}-${opp.ticker}-${i}`}
                  className={clsx(
                    'animate-fade-in',
                    opp.net_edge >= 0.10 && 'bg-green-950/10'
                  )}
                >
                  <td className="text-text-muted text-xs">{i + 1}</td>
                  <td>
                    <span className="badge badge-cyan">{opp.city}</span>
                  </td>
                  <td className="font-mono text-xs text-text-secondary">
                    {formatRange(opp)}
                  </td>
                  <td className="text-right font-mono text-xs text-text-secondary">
                    {opp.mu.toFixed(1)} / ±{opp.sigma.toFixed(1)}
                  </td>
                  <td className="text-right font-mono text-accent-purple">
                    {(opp.model_prob * 100).toFixed(1)}%
                  </td>
                  <td className="text-right font-mono text-text-secondary">
                    {(opp.ask * 100).toFixed(1)}%
                  </td>
                  <td>
                    <EdgeBar edge={opp.net_edge} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {scanner && (
        <div className="px-4 py-2 border-t border-bg-border flex justify-between text-xs text-text-muted font-mono">
          <span>{opps.length} opportunities found</span>
          <span>Cycle #{scanner.cycle_number} · {new Date(scanner.last_updated).toLocaleTimeString()}</span>
        </div>
      )}
    </div>
  );
}
