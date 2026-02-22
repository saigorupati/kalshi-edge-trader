'use client';

import { useState, useMemo } from 'react';
import { Trade } from '@/lib/api';
import { clsx } from 'clsx';
import { format, parseISO } from 'date-fns';

interface Props {
  trades: Trade[];
}

type SortKey = 'timestamp' | 'city' | 'count' | 'price_cents' | 'edge' | 'model_prob' | 'pnl' | 'dollar_risk';
type SortDir = 'asc' | 'desc';

const CITIES = ['All', 'NYC', 'LA', 'CHI', 'PHX', 'DFW'];
const RESULTS = ['All', 'WIN', 'LOSS', 'OPEN', 'EXIT'];
const STRATEGIES = ['All', 'single', 'bracket'];

function friendlyMarket(trade: Trade): string {
  const { temp_low, temp_high, is_open_low, is_open_high, ticker } = trade;
  // Try to build a label from stored bounds
  if (is_open_low  && temp_high != null) return `≤${temp_high}°`;
  if (is_open_high && temp_low  != null) return `≥${temp_low}°`;
  if (temp_low != null && temp_high != null) return `${temp_low}–${temp_high}°`;
  // Fallback: parse T{N} from ticker
  const match = ticker?.split('-').pop()?.match(/^T(\d+)$/i);
  if (match) { const n = parseInt(match[1]); return `${n}–${n + 1}°`; }
  return ticker ?? '—';
}

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) return <span className="text-text-muted ml-1">⇅</span>;
  return <span className="text-accent-cyan ml-1">{dir === 'asc' ? '↑' : '↓'}</span>;
}

export default function TradeHistory({ trades }: Props) {
  const [cityFilter,     setCityFilter]     = useState('All');
  const [resultFilter,   setResultFilter]   = useState('All');
  const [strategyFilter, setStrategyFilter] = useState('All');
  const [search,         setSearch]         = useState('');
  const [sortKey,        setSortKey]        = useState<SortKey>('timestamp');
  const [sortDir,        setSortDir]        = useState<SortDir>('desc');
  const [page,           setPage]           = useState(0);
  const PAGE_SIZE = 25;

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('desc'); }
    setPage(0);
  }

  const filtered = useMemo(() => {
    let out = trades;

    if (cityFilter !== 'All')
      out = out.filter((t) => t.city === cityFilter);

    if (strategyFilter !== 'All')
      out = out.filter((t) => (t.strategy ?? 'single') === strategyFilter);

    if (resultFilter !== 'All') {
      out = out.filter((t) => {
        if (resultFilter === 'WIN')  return t.resolved && t.resolved_yes === true;
        if (resultFilter === 'LOSS') return t.resolved && t.resolved_yes === false && (t.pnl ?? 0) < 0;
        if (resultFilter === 'EXIT') return t.resolved && t.resolved_yes === false && (t.pnl ?? 0) === 0;
        if (resultFilter === 'OPEN') return !t.resolved;
        return true;
      });
    }

    if (search.trim()) {
      const q = search.toLowerCase();
      out = out.filter((t) =>
        t.ticker?.toLowerCase().includes(q) ||
        t.city?.toLowerCase().includes(q) ||
        friendlyMarket(t).toLowerCase().includes(q)
      );
    }

    return [...out].sort((a, b) => {
      let va: number | string = 0;
      let vb: number | string = 0;
      switch (sortKey) {
        case 'timestamp':   va = a.timestamp;   vb = b.timestamp;   break;
        case 'city':        va = a.city;         vb = b.city;         break;
        case 'count':       va = a.count;        vb = b.count;        break;
        case 'price_cents': va = a.price_cents;  vb = b.price_cents;  break;
        case 'edge':        va = a.edge;         vb = b.edge;         break;
        case 'model_prob':  va = a.model_prob;   vb = b.model_prob;   break;
        case 'pnl':         va = a.pnl ?? -Infinity; vb = b.pnl ?? -Infinity; break;
        case 'dollar_risk': va = a.dollar_risk;  vb = b.dollar_risk;  break;
      }
      if (va < vb) return sortDir === 'asc' ? -1 : 1;
      if (va > vb) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });
  }, [trades, cityFilter, resultFilter, strategyFilter, search, sortKey, sortDir]);

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  // Summary stats for filtered set
  const resolved = filtered.filter((t) => t.resolved);
  const wins     = resolved.filter((t) => t.resolved_yes).length;
  const totalPnl = resolved.reduce((s, t) => s + (t.pnl ?? 0), 0);
  const winRate  = resolved.length > 0 ? (wins / resolved.length) * 100 : null;

  function Th({ label, k }: { label: string; k: SortKey }) {
    return (
      <th onClick={() => toggleSort(k)} className="cursor-pointer select-none hover:text-text-primary">
        {label}<SortIcon active={sortKey === k} dir={sortDir} />
      </th>
    );
  }

  return (
    <div className="card flex flex-col">
      {/* Header */}
      <div className="card-header flex-wrap gap-2">
        <span className="card-title">Trade History</span>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="badge badge-cyan">{filtered.length} trades</span>
          {winRate !== null && (
            <span className={`badge ${winRate >= 50 ? 'badge-green' : 'badge-red'}`}>
              {winRate.toFixed(0)}% win
            </span>
          )}
          <span className={`badge ${totalPnl >= 0 ? 'badge-green' : 'badge-red'}`}>
            {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
          </span>
        </div>
      </div>

      {/* Filters */}
      <div className="px-4 py-3 border-b border-bg-border flex flex-wrap gap-2 items-center">
        {/* Search */}
        <input
          type="text"
          placeholder="Search ticker / city / range…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(0); }}
          className="bg-bg-secondary border border-bg-border rounded px-2 py-1 text-xs font-mono
                     text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-cyan/50
                     w-44"
        />

        {/* City */}
        <select
          value={cityFilter}
          onChange={(e) => { setCityFilter(e.target.value); setPage(0); }}
          className="bg-bg-secondary border border-bg-border rounded px-2 py-1 text-xs font-mono
                     text-text-primary focus:outline-none focus:border-accent-cyan/50"
        >
          {CITIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>

        {/* Result */}
        <select
          value={resultFilter}
          onChange={(e) => { setResultFilter(e.target.value); setPage(0); }}
          className="bg-bg-secondary border border-bg-border rounded px-2 py-1 text-xs font-mono
                     text-text-primary focus:outline-none focus:border-accent-cyan/50"
        >
          {RESULTS.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>

        {/* Strategy */}
        <select
          value={strategyFilter}
          onChange={(e) => { setStrategyFilter(e.target.value); setPage(0); }}
          className="bg-bg-secondary border border-bg-border rounded px-2 py-1 text-xs font-mono
                     text-text-primary focus:outline-none focus:border-accent-cyan/50"
        >
          {STRATEGIES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>

        <button
          onClick={() => { setCityFilter('All'); setResultFilter('All'); setStrategyFilter('All'); setSearch(''); setPage(0); }}
          className="btn-ghost text-xs px-2 py-1"
        >
          Reset
        </button>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {paged.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-text-muted text-sm font-mono">
            No trades match filters
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <Th label="Time"      k="timestamp"   />
                <Th label="City"      k="city"        />
                <th>Market</th>
                <th>Strategy</th>
                <Th label="Qty"       k="count"       />
                <Th label="Entry"     k="price_cents" />
                <Th label="Model%"    k="model_prob"  />
                <Th label="Edge%"     k="edge"        />
                <Th label="Risk$"     k="dollar_risk" />
                <Th label="P&L"       k="pnl"         />
                <th className="text-center">Result</th>
              </tr>
            </thead>
            <tbody>
              {paged.map((t) => {
                const time = (() => {
                  try { return format(parseISO(t.timestamp), 'MM/dd HH:mm'); }
                  catch { return t.timestamp?.slice(0, 16) ?? '—'; }
                })();
                const won  = t.resolved && t.resolved_yes === true;
                const lost = t.resolved && t.resolved_yes === false && (t.pnl ?? 0) < 0;
                const exit = t.resolved && !t.resolved_yes && (t.pnl ?? 0) === 0;

                return (
                  <tr key={t.trade_id}>
                    <td className="text-text-muted text-xs whitespace-nowrap">{time}</td>
                    <td><span className="badge badge-cyan">{t.city}</span></td>
                    <td className="font-mono text-xs text-text-secondary whitespace-nowrap">
                      {friendlyMarket(t)}
                    </td>
                    <td>
                      <span className={`badge ${t.strategy === 'bracket' ? 'badge-purple' : 'badge-cyan'}`}>
                        {t.strategy ?? 'single'}
                      </span>
                    </td>
                    <td className="font-mono text-right">{t.count}</td>
                    <td className="font-mono text-right">${(t.price_cents / 100).toFixed(2)}</td>
                    <td className="font-mono text-right text-accent-cyan">
                      {(t.model_prob * 100).toFixed(1)}%
                    </td>
                    <td className={clsx(
                      'font-mono text-right',
                      t.edge >= 0.05 ? 'text-accent-green' : t.edge >= 0.02 ? 'text-accent-yellow' : 'text-accent-red'
                    )}>
                      {(t.edge * 100).toFixed(1)}%
                    </td>
                    <td className="font-mono text-right text-text-secondary">
                      ${(t.dollar_risk ?? 0).toFixed(2)}
                    </td>
                    <td className={clsx(
                      'font-mono text-right font-semibold',
                      !t.resolved ? 'text-text-muted'
                        : (t.pnl ?? 0) > 0 ? 'text-accent-green'
                        : (t.pnl ?? 0) < 0 ? 'text-accent-red'
                        : 'text-text-muted'
                    )}>
                      {t.resolved && t.pnl !== undefined && t.pnl !== null
                        ? `${t.pnl >= 0 ? '+' : ''}$${t.pnl.toFixed(2)}`
                        : '—'}
                    </td>
                    <td className="text-center">
                      {!t.resolved ? (
                        <span className="badge badge-yellow">OPEN</span>
                      ) : won ? (
                        <span className="badge badge-green">WIN</span>
                      ) : lost ? (
                        <span className="badge badge-red">LOSS</span>
                      ) : exit ? (
                        <span className="badge badge-cyan">EXIT</span>
                      ) : (
                        <span className="badge badge-cyan">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="px-4 py-3 border-t border-bg-border flex items-center justify-between text-xs font-mono text-text-muted">
          <span>{page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, filtered.length)} of {filtered.length}</span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="btn-ghost px-2 py-1 disabled:opacity-30"
            >← Prev</button>
            <span className="px-2 py-1">{page + 1} / {totalPages}</span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="btn-ghost px-2 py-1 disabled:opacity-30"
            >Next →</button>
          </div>
        </div>
      )}
    </div>
  );
}
