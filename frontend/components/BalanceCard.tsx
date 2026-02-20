'use client';

import { BalanceData, PnLToday, RiskStatus } from '@/lib/api';
import { clsx } from 'clsx';

interface Props {
  balance: BalanceData | null;
  pnlToday: PnLToday | null;
  risk: RiskStatus | null;
  lastUpdated: Date | null;
}

function StatCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: 'green' | 'red' | 'cyan' | 'yellow' | 'purple';
}) {
  const colorMap = {
    green:  'text-accent-green',
    red:    'text-accent-red',
    cyan:   'text-accent-cyan',
    yellow: 'text-accent-yellow',
    purple: 'text-accent-purple',
  };
  return (
    <div className="card px-5 py-4 flex flex-col gap-1">
      <span className="card-title">{label}</span>
      <span
        className={clsx(
          'stat-value mt-1',
          accent ? colorMap[accent] : 'text-text-primary'
        )}
      >
        {value}
      </span>
      {sub && <span className="text-xs text-text-muted font-mono mt-0.5">{sub}</span>}
    </div>
  );
}

export default function BalanceCard({ balance, pnlToday, risk, lastUpdated }: Props) {
  const totalReturnPct = balance?.total_return_pct ?? 0;
  const todayPnl = pnlToday?.realized_pnl ?? 0;
  const winRate = pnlToday?.win_rate ?? 0;
  const openPos = risk?.open_positions ?? 0;
  const killSwitch = risk?.kill_switch_active ?? false;

  const returnAccent: 'green' | 'red' =
    totalReturnPct >= 0 ? 'green' : 'red';
  const pnlAccent: 'green' | 'red' = todayPnl >= 0 ? 'green' : 'red';

  return (
    <div className="flex flex-col gap-2">
      {/* Top row: 4 stat cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <StatCard
          label="Balance"
          value={balance ? `$${balance.balance.toFixed(2)}` : '—'}
          sub={`Started $${balance?.starting_balance.toFixed(0) ?? '—'}`}
          accent="cyan"
        />
        <StatCard
          label="Total Return"
          value={balance ? `${totalReturnPct >= 0 ? '+' : ''}${totalReturnPct.toFixed(2)}%` : '—'}
          sub={`Mode: ${balance?.mode ?? '—'}`}
          accent={returnAccent}
        />
        <StatCard
          label="Today's P&L"
          value={pnlToday ? `${todayPnl >= 0 ? '+' : ''}$${todayPnl.toFixed(2)}` : '—'}
          sub={
            pnlToday
              ? `${pnlToday.win_count}W / ${pnlToday.loss_count}L`
              : 'No trades today'
          }
          accent={pnlAccent}
        />
        <StatCard
          label="Win Rate (today)"
          value={pnlToday ? `${winRate.toFixed(0)}%` : '—'}
          sub={`${openPos} open position${openPos !== 1 ? 's' : ''}`}
          accent={
            winRate >= 80 ? 'green' : winRate >= 60 ? 'yellow' : 'red'
          }
        />
      </div>

      {/* Status bar */}
      <div className="card px-4 py-2 flex items-center gap-4 text-xs font-mono">
        {killSwitch ? (
          <>
            <span className="dot-red" />
            <span className="text-accent-red font-semibold">KILL SWITCH ACTIVE — Trading halted</span>
          </>
        ) : (
          <>
            <span className="dot-green" />
            <span className="text-accent-green font-semibold">ACTIVE</span>
          </>
        )}
        <span className="text-text-muted ml-auto">
          {lastUpdated
            ? `Updated ${lastUpdated.toLocaleTimeString()}`
            : 'Connecting…'}
        </span>
      </div>
    </div>
  );
}
