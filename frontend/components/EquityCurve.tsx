'use client';

import { PnLRecord } from '@/lib/api';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from 'recharts';
import { format, parseISO } from 'date-fns';

interface Props {
  history: PnLRecord[];
}

interface ChartPoint {
  date: string;
  balance: number;
  pnl: number;
  killSwitch: boolean;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload as ChartPoint;
  return (
    <div className="bg-bg-card border border-bg-border rounded-lg px-3 py-2 text-xs font-mono shadow-xl">
      <p className="text-text-secondary mb-1">{label}</p>
      <p className="text-accent-cyan">Balance: ${d.balance.toFixed(2)}</p>
      <p className={d.pnl >= 0 ? 'text-accent-green' : 'text-accent-red'}>
        Daily P&L: {d.pnl >= 0 ? '+' : ''}${d.pnl.toFixed(2)}
      </p>
      {d.killSwitch && (
        <p className="text-accent-red mt-1">⚠ Kill switch triggered</p>
      )}
    </div>
  );
}

export default function EquityCurve({ history }: Props) {
  const data: ChartPoint[] = history.map((r) => ({
    date: (() => {
      try { return format(parseISO(r.date), 'MMM d'); } catch { return r.date; }
    })(),
    balance: r.ending_balance,
    pnl: r.realized_pnl,
    killSwitch: r.kill_switch_triggered,
  }));

  const startBalance = history[0]?.starting_balance ?? 1000;
  const endBalance = history[history.length - 1]?.ending_balance ?? startBalance;
  const totalReturn = ((endBalance - startBalance) / startBalance) * 100;

  return (
    <div className="card h-full flex flex-col">
      <div className="card-header">
        <span className="card-title">Equity Curve</span>
        <span className={`text-sm font-mono font-semibold ${totalReturn >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
          {totalReturn >= 0 ? '+' : ''}{totalReturn.toFixed(2)}%
        </span>
      </div>

      <div className="flex-1 p-3 min-h-[200px]">
        {data.length === 0 ? (
          <div className="flex items-center justify-center h-full text-text-muted text-sm font-mono">
            No history yet — run a few cycles first
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e1e30" />
              <XAxis
                dataKey="date"
                tick={{ fill: '#555577', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#555577', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => `$${v}`}
                width={60}
              />
              <Tooltip content={<CustomTooltip />} />
              <ReferenceLine y={startBalance} stroke="#555577" strokeDasharray="4 4" />
              <Line
                type="monotone"
                dataKey="balance"
                stroke="#00d4ff"
                strokeWidth={2}
                dot={(props) => {
                  const { cx, cy, payload } = props;
                  if (payload.killSwitch) {
                    return <circle key={`dot-${cx}-${cy}`} cx={cx} cy={cy} r={4} fill="#ff3366" stroke="none" />;
                  }
                  return <circle key={`dot-${cx}-${cy}`} cx={cx} cy={cy} r={2} fill="#00d4ff" stroke="none" />;
                }}
                activeDot={{ r: 5, fill: '#00d4ff', stroke: '#0a0a0f', strokeWidth: 2 }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
