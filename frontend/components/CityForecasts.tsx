'use client';

import { ScannerState } from '@/lib/api';
import { clsx } from 'clsx';

interface Props {
  scanner: ScannerState | null;
}

const CITY_LABELS: Record<string, string> = {
  LA:  'Los Angeles',
  NYC: 'New York',
  MIA: 'Miami',
  CHI: 'Chicago',
  PHX: 'Phoenix',
};

export default function CityForecasts({ scanner }: Props) {
  const distributions = scanner?.city_distributions ?? {};
  const opportunities = scanner?.opportunities ?? [];

  // Best edge per city
  const bestEdge: Record<string, number> = {};
  for (const opp of opportunities) {
    if (bestEdge[opp.city] === undefined || opp.net_edge > bestEdge[opp.city]) {
      bestEdge[opp.city] = opp.net_edge;
    }
  }

  const cities = Object.keys(CITY_LABELS);

  return (
    <div className="card h-full flex flex-col">
      <div className="card-header">
        <span className="card-title">City Forecasts</span>
        {scanner && (
          <span className="text-xs text-text-muted font-mono">
            Cycle #{scanner.cycle_number}
          </span>
        )}
      </div>

      <div className="flex-1 overflow-auto">
        {!scanner ? (
          <div className="flex items-center justify-center h-32 text-text-muted text-sm font-mono">
            Waiting for first cycle…
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>City</th>
                <th className="text-right">μ (°F)</th>
                <th className="text-right">σ</th>
                <th className="text-right">Bias</th>
                <th className="text-right">Best Edge</th>
              </tr>
            </thead>
            <tbody>
              {cities.map((city) => {
                const dist = distributions[city];
                const edge = bestEdge[city];
                return (
                  <tr key={city}>
                    <td>
                      <div className="flex flex-col">
                        <span className="font-semibold text-accent-cyan">{city}</span>
                        <span className="text-[10px] text-text-muted">{CITY_LABELS[city]}</span>
                      </div>
                    </td>
                    <td className="text-right font-mono">
                      {dist ? `${dist.mu.toFixed(1)}°` : '—'}
                    </td>
                    <td className="text-right font-mono text-text-secondary">
                      {dist ? `±${dist.sigma.toFixed(1)}` : '—'}
                    </td>
                    <td className="text-right font-mono">
                      {dist ? (
                        <span className={dist.bias_correction >= 0 ? 'text-accent-green' : 'text-accent-red'}>
                          {dist.bias_correction >= 0 ? '+' : ''}
                          {dist.bias_correction.toFixed(2)}°
                        </span>
                      ) : (
                        <span className="text-text-muted">—</span>
                      )}
                    </td>
                    <td className="text-right">
                      {edge !== undefined ? (
                        <span
                          className={clsx(
                            'font-mono font-semibold',
                            edge >= 0.08
                              ? 'text-accent-green'
                              : edge >= 0.05
                              ? 'text-accent-yellow'
                              : 'text-accent-red'
                          )}
                        >
                          {(edge * 100).toFixed(1)}%
                        </span>
                      ) : (
                        <span className="text-text-muted text-xs">no edge</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {scanner && (
        <div className="px-4 py-2 border-t border-bg-border text-xs text-text-muted font-mono">
          Last scan:{' '}
          {new Date(scanner.last_updated).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
