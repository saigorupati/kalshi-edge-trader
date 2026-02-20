'use client';

import { RiskStatus as RiskStatusType } from '@/lib/api';
import { clsx } from 'clsx';

interface Props {
  risk: RiskStatusType | null;
}

function ExposureBar({ city, value, max }: { city: string; value: number; max: number }) {
  const pct = Math.min((value / max) * 100, 100);
  const color =
    pct >= 90 ? '#ff3366' : pct >= 60 ? '#ffcc00' : '#00ff88';

  return (
    <div className="flex items-center gap-3 text-xs font-mono">
      <span className="w-8 text-text-secondary">{city}</span>
      <div className="flex-1 bg-bg-border rounded-full h-1.5 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="w-14 text-right text-text-secondary">
        ${value.toFixed(0)} / ${max.toFixed(0)}
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

  const dailyLossPercent = risk.daily_stop_loss_pct * 100;
  const currentLossPercent =
    risk.daily_loss < 0
      ? Math.min((Math.abs(risk.daily_loss) / (risk.daily_stop_loss_pct * 1)) * 100, 100)
      : 0;

  const positionsPct = (risk.open_positions / risk.max_open_positions) * 100;

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
              risk.open_positions >= risk.max_open_positions ? 'text-accent-red' : 'text-text-primary'
            )}>
              {risk.open_positions} / {risk.max_open_positions}
            </span>
          </div>
          <div className="bg-bg-border rounded-full h-1.5 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${positionsPct}%`,
                backgroundColor: positionsPct >= 100 ? '#ff3366' : positionsPct >= 60 ? '#ffcc00' : '#00d4ff',
              }}
            />
          </div>
        </div>

        {/* Daily loss meter */}
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs font-mono text-text-secondary">
            <span>Daily Loss Exposure</span>
            <span className={clsx('font-semibold', risk.daily_loss < 0 ? 'text-accent-red' : 'text-accent-green')}>
              {risk.daily_loss < 0 ? '-' : '+'}${Math.abs(risk.daily_loss).toFixed(2)}
              &nbsp;/&nbsp;{dailyLossPercent.toFixed(0)}% limit
            </span>
          </div>
          <div className="bg-bg-border rounded-full h-1.5 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${currentLossPercent}%`,
                backgroundColor: currentLossPercent >= 80 ? '#ff3366' : '#ffcc00',
              }}
            />
          </div>
        </div>

        {/* Per-city exposure */}
        {Object.keys(risk.city_exposure).length > 0 && (
          <div className="space-y-2">
            <span className="text-xs font-mono text-text-muted tracking-widest uppercase">City Exposure</span>
            <div className="space-y-2">
              {Object.entries(risk.city_exposure).map(([city, val]) => (
                <ExposureBar
                  key={city}
                  city={city}
                  value={val}
                  max={risk.max_city_exposure_pct}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
