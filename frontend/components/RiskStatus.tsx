'use client';

import { RiskStatus as RiskStatusType } from '@/lib/api';
import { clsx } from 'clsx';

interface Props {
  risk: RiskStatusType | null;
}

function ExposureBar({
  city,
  pctUsed,
  exposure,
  budget,
}: {
  city: string;
  pctUsed: number;
  exposure: number;
  budget: number;
}) {
  const color =
    pctUsed >= 90 ? '#ff3366' : pctUsed >= 60 ? '#ffcc00' : '#00ff88';

  return (
    <div className="flex items-center gap-3 text-xs font-mono">
      <span className="w-8 text-text-secondary">{city}</span>
      <div className="flex-1 bg-bg-border rounded-full h-1.5 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${Math.min(pctUsed, 100)}%`, backgroundColor: color }}
        />
      </div>
      <span className="w-24 text-right text-text-secondary">
        ${exposure.toFixed(0)} / ${budget.toFixed(0)}
      </span>
    </div>
  );
}

export default function RiskStatus({ risk }: Props) {
  if (!risk) {
    return (
      <div className="card h-full flex flex-col">
        <div className="card-header">
          <span className="card-title">Risk Status</span>
        </div>
        <div className="flex items-center justify-center flex-1 text-text-muted text-sm font-mono">
          Loading…
        </div>
      </div>
    );
  }

  const positionsPct = risk.max_positions > 0
    ? (risk.open_positions / risk.max_positions) * 100
    : 0;

  // Approximate daily loss from threshold vs start balance
  const dailyLossDollars = risk.day_start_balance - risk.stop_loss_threshold;
  const lossLimit = dailyLossDollars > 0 ? dailyLossDollars : risk.day_start_balance * (risk.daily_stop_loss_pct / 100);

  // We don't have current daily loss in dollars directly; use city exposure as a proxy
  // Just show the stop-loss threshold info instead
  const stopLossPct = risk.daily_stop_loss_pct;

  return (
    <div className="card h-full flex flex-col">
      <div className="card-header">
        <span className="card-title">Risk Status</span>
        {risk.kill_switch_active ? (
          <span className="badge badge-red">KILL SWITCH</span>
        ) : (
          <span className="badge badge-green">NORMAL</span>
        )}
      </div>

      <div className="p-4 space-y-5 flex-1 overflow-auto">
        {/* Kill switch banner */}
        {risk.kill_switch_active && (
          <div className="bg-red-950/40 border border-accent-red/40 rounded-lg px-4 py-3 text-sm text-accent-red font-mono">
            ⚠ Daily loss limit hit. Trading halted until tomorrow 00:00 UTC.
          </div>
        )}

        {/* Positions used */}
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs font-mono text-text-secondary">
            <span>Open Positions</span>
            <span className={clsx(
              'font-semibold',
              risk.open_positions >= risk.max_positions ? 'text-accent-red' : 'text-text-primary'
            )}>
              {risk.open_positions} / {risk.max_positions}
            </span>
          </div>
          <div className="bg-bg-border rounded-full h-1.5 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${Math.min(positionsPct, 100)}%`,
                backgroundColor: positionsPct >= 100 ? '#ff3366' : positionsPct >= 60 ? '#ffcc00' : '#00d4ff',
              }}
            />
          </div>
        </div>

        {/* Stop loss info */}
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs font-mono text-text-secondary">
            <span>Stop-Loss Threshold</span>
            <span className="font-semibold text-text-primary">
              -{stopLossPct.toFixed(0)}% / ${risk.stop_loss_threshold.toFixed(2)}
            </span>
          </div>
          <div className="text-xs text-text-muted font-mono">
            Day start: ${risk.day_start_balance.toFixed(2)} · Max loss: ${lossLimit.toFixed(2)}
          </div>
        </div>

        {/* Per-city exposure */}
        {Object.keys(risk.city_exposure).length > 0 && (
          <div className="space-y-2">
            <span className="text-xs font-mono text-text-muted tracking-widest uppercase">
              City Exposure
            </span>
            <div className="space-y-2">
              {Object.entries(risk.city_exposure).map(([city, detail]) => (
                <ExposureBar
                  key={city}
                  city={city}
                  pctUsed={detail.pct_used}
                  exposure={detail.exposure}
                  budget={detail.budget}
                />
              ))}
            </div>
          </div>
        )}

        {/* Mode badge */}
        <div className="pt-1">
          <span className={clsx(
            'badge',
            risk.mode === 'live' ? 'badge-red' : 'badge-cyan'
          )}>
            {risk.mode?.toUpperCase() ?? 'PAPER'}
          </span>
        </div>
      </div>
    </div>
  );
}
