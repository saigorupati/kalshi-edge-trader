/**
 * Typed API client for the Kalshi Edge Trader FastAPI backend.
 * Reads NEXT_PUBLIC_API_URL from env (falls back to '' so Next.js rewrites work in dev).
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? '';

// ── Types ─────────────────────────────────────────────────────────

export interface BalanceData {
  balance: number;
  starting_balance: number;
  total_return_pct: number;
  mode: string;
}

export interface OpenPosition {
  trade_id: string;
  city: string;
  ticker: string;
  side: string;
  count: number;
  price_cents: number;
  model_prob: number;
  edge: number;
  timestamp: string;
  order_id?: string;
  unrealized_pnl?: number;
  market_yes_bid?: number;
}

export interface Trade {
  trade_id: string;
  city: string;
  ticker: string;
  side: string;
  action: string;
  count: number;
  price_cents: number;
  model_prob: number;
  edge: number;
  kelly_fraction: number;
  dollar_risk: number;
  mode: string;
  order_id?: string;
  resolved: boolean;
  resolved_yes?: boolean;
  pnl?: number;
  timestamp: string;
}

export interface PnLToday {
  date: string;
  win_count: number;
  loss_count: number;
  realized_pnl: number;
  win_rate: number;
  trade_count: number;
}

export interface PnLRecord {
  date: string;
  starting_balance: number;
  ending_balance: number;
  realized_pnl: number;
  win_count: number;
  loss_count: number;
  kill_switch_triggered: boolean;
}

export interface RiskStatus {
  kill_switch_active: boolean;
  daily_loss: number;
  open_positions: number;
  max_open_positions: number;
  city_exposure: Record<string, number>;
  max_city_exposure_pct: number;
  daily_stop_loss_pct: number;
}

export interface MarketInfo {
  ticker: string;
  title: string;
  yes_ask?: number;
  yes_bid?: number;
  volume?: number;
  temp_low?: number;
  temp_high?: number;
}

export interface CalibrationRecord {
  city: string;
  forecast_date: string;
  nbm_mu: number;
  nbm_sigma: number;
  actual_high?: number;
  bias?: number;
}

export interface Opportunity {
  city: string;
  ticker: string;
  temp_low?: number;
  temp_high?: number;
  is_open_low: boolean;
  is_open_high: boolean;
  model_prob: number;
  ask: number;
  net_edge: number;
  mu: number;
  sigma: number;
}

export interface ScannerState {
  cycle_number: number;
  last_updated: string;
  opportunities: Opportunity[];
  city_distributions: Record<string, { mu: number; sigma: number; bias_correction: number }>;
}

export interface HealthData {
  status: string;
  mode: string;
  uptime_seconds: number;
  cycle_count: number;
}

// ── Fetch helper ─────────────────────────────────────────────────

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`API ${res.status}: ${body || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

// ── Endpoints ────────────────────────────────────────────────────

export const api = {
  health:           ()                   => apiFetch<HealthData>('/api/health'),
  balance:          ()                   => apiFetch<BalanceData>('/api/balance'),
  openPositions:    ()                   => apiFetch<OpenPosition[]>('/api/positions/open'),
  trades:           (date?: string, city?: string) => {
    const params = new URLSearchParams();
    if (date) params.set('date', date);
    if (city) params.set('city', city);
    const qs = params.toString();
    return apiFetch<Trade[]>(`/api/trades${qs ? `?${qs}` : ''}`);
  },
  pnlToday:         ()                   => apiFetch<PnLToday>('/api/pnl/today'),
  pnlHistory:       ()                   => apiFetch<PnLRecord[]>('/api/pnl/history'),
  riskStatus:       ()                   => apiFetch<RiskStatus>('/api/risk/status'),
  markets:          (city: string)       => apiFetch<MarketInfo[]>(`/api/markets/${city}`),
  calibration:      (city: string)       => apiFetch<CalibrationRecord[]>(`/api/calibration/${city}`),
  scanner:          ()                   => apiFetch<ScannerState>('/api/scanner'),
  cancelOrder: (orderId: string, tradeId?: string) => {
    const qs = tradeId ? `?trade_id=${encodeURIComponent(tradeId)}` : '';
    return apiFetch<{ success: boolean; message: string }>(
      `/api/orders/${encodeURIComponent(orderId)}${qs}`,
      { method: 'DELETE' }
    );
  },
};
