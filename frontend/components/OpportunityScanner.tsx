'use client';

import { useState } from 'react';
import { ScannerState, Opportunity, BracketOpportunity } from '@/lib/api';
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
  if (opp.is_open_low  && opp.temp_high != null) return `≤ ${opp.temp_high}°F`;
  if (opp.is_open_high && opp.temp_low  != null) return `≥ ${opp.temp_low}°F`;
  if (opp.temp_low != null && opp.temp_high != null) return `${opp.temp_low}–${opp.temp_high}°F`;
  // Fallback: shouldn't happen after server fix, but avoids "undefined–undefined"
  return '—';
}

export default function OpportunityScanner({ scanner, wsConnected }: Props) {
  const [bracketExpanded, setBracketExpanded] = useState(true);

  const opps: Opportunity[] = [...(scanner?.opportunities ?? [])].sort(
    (a, b) => b.net_edge - a.net_edge
  );
  const brackets: BracketOpportunity[] = [...(scanner?.bracket_opportunities ?? [])].sort(
    (a, b) => b.expected_value - a.expected_value
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
        ) : (
          <>
            {/* ── Single-bin table ── */}
            {opps.length === 0 ? (
              <div className="flex items-center justify-center h-24 text-text-muted text-sm font-mono">
                No single-bin edges found this cycle
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

            {/* ── Bracket opportunities panel ── */}
            <div className="border-t border-bg-border mt-1">
              <button
                onClick={() => setBracketExpanded(v => !v)}
                className="w-full flex items-center justify-between px-4 py-2 text-xs font-mono text-text-secondary hover:text-text-primary transition-colors"
              >
                <span className="flex items-center gap-2">
                  <span
                    className={clsx(
                      'inline-block transition-transform duration-200',
                      bracketExpanded ? 'rotate-90' : 'rotate-0'
                    )}
                  >
                    ▶
                  </span>
                  <span className="text-accent-orange font-semibold">Bracket Opportunities</span>
                  {brackets.length > 0 && (
                    <span className="badge badge-cyan">{brackets.length}</span>
                  )}
                </span>
                <span className="text-text-muted">2-bin straddle · buys both legs</span>
              </button>

              {bracketExpanded && (
                brackets.length === 0 ? (
                  <div className="flex items-center justify-center h-16 text-text-muted text-xs font-mono px-4">
                    No bracket edges this cycle — need 2 adjacent bins each with &gt;5% edge + combined &gt;10%
                  </div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>City</th>
                        <th>Leg 1</th>
                        <th className="text-right">Ask₁</th>
                        <th>Leg 2</th>
                        <th className="text-right">Ask₂</th>
                        <th className="text-right">Combined%</th>
                        <th className="text-right">Total Cost</th>
                        <th>EV</th>
                      </tr>
                    </thead>
                    <tbody>
                      {brackets.map((b, i) => (
                        <tr
                          key={`bracket-${b.city}-${b.leg1_ticker}-${i}`}
                          className="animate-fade-in bg-orange-950/10"
                        >
                          <td>
                            <span className="badge badge-cyan">{b.city}</span>
                          </td>
                          <td className="font-mono text-xs text-text-secondary">
                            {b.leg1_temp_low != null && b.leg1_temp_high != null
                              ? `${b.leg1_temp_low}–${b.leg1_temp_high}°F`
                              : b.leg1_range}
                          </td>
                          <td className="text-right font-mono text-xs text-text-secondary">
                            {(b.leg1_ask * 100).toFixed(1)}¢
                          </td>
                          <td className="font-mono text-xs text-text-secondary">
                            {b.leg2_temp_low != null && b.leg2_temp_high != null
                              ? `${b.leg2_temp_low}–${b.leg2_temp_high}°F`
                              : b.leg2_range}
                          </td>
                          <td className="text-right font-mono text-xs text-text-secondary">
                            {(b.leg2_ask * 100).toFixed(1)}¢
                          </td>
                          <td className="text-right font-mono text-accent-purple">
                            {(b.combined_model_prob * 100).toFixed(1)}%
                          </td>
                          <td className="text-right font-mono text-text-secondary">
                            {(b.total_ask * 100).toFixed(1)}¢
                          </td>
                          <td>
                            <EdgeBar edge={b.expected_value} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )
              )}
            </div>
          </>
        )}
      </div>

      {scanner && (
        <div className="px-4 py-2 border-t border-bg-border flex justify-between text-xs text-text-muted font-mono">
          <span>{opps.length} single · {brackets.length} bracket</span>
          <span>Cycle #{scanner.cycle_number} · {new Date(scanner.last_updated).toLocaleTimeString()}</span>
        </div>
      )}
    </div>
  );
}
