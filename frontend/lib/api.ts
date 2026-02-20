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
  current_price?: number;
  market_yes_bid?: number;
  // Temperature bucket bounds (present on trades placed after this field was added)
  temp_low?: number | null;
  temp_high?: number | null;
  is_open_low?: boolean;
  is_open_high?: boolean;
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
  open_positions: number;
  realized_pnl: number;
  win_rate: number | null;   // null when no resolved trades yet
  total_trades: number;
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

export interface CityExposureDetail {
  display_name: string;
  exposure: number;
  budget: number;
  pct_used: number;
}

export interface RiskStatus {
  kill_switch_active: boolean;
  open_positions: number;
  max_positions: number;
  day_start_balance: number;
  daily_stop_loss_pct: number;
  stop_loss_threshold: number;
  city_exposure: Record<string, CityExposureDetail>;
  mode: string;
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

export interface BracketOpportunity {
  city: string;
  leg1_ticker: string;
  leg1_range: string;
  leg1_temp_low?: number;
  leg1_temp_high?: number;
  leg1_ask: number;
  leg1_model_prob: number;
  leg1_net_edge: number;
  leg2_ticker: string;
  leg2_range: string;
  leg2_temp_low?: number;
  leg2_temp_high?: number;
  leg2_ask: number;
  leg2_model_prob: number;
  leg2_net_edge: number;
  combined_model_prob: number;
  total_ask: number;
  profit_if_hit: number;
  total_net_edge: number;
  expected_value: number;
  has_edge: boolean;
}

export interface ScannerState {
  cycle_number: number;
  last_updated: string;
  opportunities: Opportunity[];
  bracket_opportunities: BracketOpportunity[];
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
    headers: { 'Content-Type': 'application/json', ...options?.headers as Record<string, string> },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`API ${res.status}: ${body || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export interface LimitSellResult {
  ticker: string;
  trade_id: string;
  order_id?: string;
  sell_price_cents: number;
  count: number;
  pnl: number;
  mode: string;
  status: string;
}

// ── Endpoints ────────────────────────────────────────────────────

// ── Envelope unwrappers (server wraps arrays in {positions:[...], trades:[...], etc.}) ──

async function fetchPositions(): Promise<OpenPosition[]> {
  const res = await apiFetch<{ positions: OpenPosition[]; count: number }>('/api/positions/open');
  return res.positions ?? [];
}

async function fetchTrades(date?: string, city?: string): Promise<Trade[]> {
  const params = new URLSearchParams();
  if (date) params.set('date', date);
  if (city) params.set('city', city);
  const qs = params.toString();
  const res = await apiFetch<{ trades: Trade[]; count: number }>(`/api/trades${qs ? `?${qs}` : ''}`);
  return res.trades ?? [];
}

async function fetchPnlHistory(): Promise<PnLRecord[]> {
  const res = await apiFetch<{ history: PnLRecord[]; count: number }>('/api/pnl/history');
  return res.history ?? [];
}

// Scanner: server returns opportunities as dict-by-city, flatten to arrays
async function fetchScanner(): Promise<ScannerState> {
  const raw = await apiFetch<{
    last_updated: string | null;
    cycle_number: number;
    opportunities: Record<string, Array<{
      ticker: string;
      temp_low?: number;
      temp_high?: number;
      is_open_low: boolean;
      is_open_high: boolean;
      model_prob: number;
      ask_price: number;
      net_edge: number;
      has_edge: boolean;
    }>>;
    dist_by_city: Record<string, { mu: number; sigma: number; bias_applied: number }>;
    bracket_opportunities?: Record<string, Array<{
      leg1_ticker: string;
      leg1_range: string;
      leg1_temp_low?: number;
      leg1_temp_high?: number;
      leg1_ask: number;
      leg1_model_prob: number;
      leg1_net_edge: number;
      leg2_ticker: string;
      leg2_range: string;
      leg2_temp_low?: number;
      leg2_temp_high?: number;
      leg2_ask: number;
      leg2_model_prob: number;
      leg2_net_edge: number;
      combined_model_prob: number;
      total_ask: number;
      profit_if_hit: number;
      total_net_edge: number;
      expected_value: number;
      has_edge: boolean;
    }>>;
  }>('/api/scanner');

  // Flatten dict-by-city into a sorted array of Opportunity
  const opps: Opportunity[] = [];
  for (const [city, cityOpps] of Object.entries(raw.opportunities ?? {})) {
    for (const o of cityOpps) {
      opps.push({
        city,
        ticker:       o.ticker,
        temp_low:     o.temp_low,
        temp_high:    o.temp_high,
        is_open_low:  o.is_open_low  ?? false,
        is_open_high: o.is_open_high ?? false,
        model_prob:   o.model_prob,
        ask:          o.ask_price,
        net_edge:     o.net_edge,
        mu:           raw.dist_by_city?.[city]?.mu    ?? 0,
        sigma:        raw.dist_by_city?.[city]?.sigma  ?? 0,
      });
    }
  }
  opps.sort((a, b) => b.net_edge - a.net_edge);

  // Flatten bracket opportunities dict-by-city into sorted array
  const brackets: BracketOpportunity[] = [];
  for (const [city, cityBrackets] of Object.entries(raw.bracket_opportunities ?? {})) {
    for (const b of cityBrackets) {
      brackets.push({ city, ...b });
    }
  }
  brackets.sort((a, b) => b.expected_value - a.expected_value);

  // Normalise city_distributions shape
  const dists: ScannerState['city_distributions'] = {};
  for (const [city, d] of Object.entries(raw.dist_by_city ?? {})) {
    dists[city] = { mu: d.mu, sigma: d.sigma, bias_correction: d.bias_applied };
  }

  return {
    cycle_number:          raw.cycle_number ?? 0,
    last_updated:          raw.last_updated ?? new Date().toISOString(),
    opportunities:         opps,
    bracket_opportunities: brackets,
    city_distributions:    dists,
  };
}

export const api = {
  health:        ()                          => apiFetch<HealthData>('/api/health'),
  balance:       ()                          => apiFetch<BalanceData>('/api/balance'),
  openPositions: ()                          => fetchPositions(),
  trades:        (date?: string, city?: string) => fetchTrades(date, city),
  pnlToday:      ()                          => apiFetch<PnLToday>('/api/pnl/today'),
  pnlHistory:    ()                          => fetchPnlHistory(),
  riskStatus:    ()                          => apiFetch<RiskStatus>('/api/risk/status'),
  markets:       (city: string)              => apiFetch<MarketInfo[]>(`/api/markets/${city}`),
  calibration:   (city: string)              => apiFetch<CalibrationRecord[]>(`/api/calibration/${city}`),
  scanner:       ()                          => fetchScanner(),
  cancelOrder: (orderId: string, tradeId?: string) => {
    const qs = tradeId ? `?trade_id=${encodeURIComponent(tradeId)}` : '';
    return apiFetch<{ success: boolean; message: string }>(
      `/api/orders/${encodeURIComponent(orderId)}${qs}`,
      { method: 'DELETE' }
    );
  },
  limitSell: (ticker: string, tradeId: string, priceCents: number) =>
    apiFetch<LimitSellResult>(
      `/api/orders/${encodeURIComponent(ticker)}/sell`,
      {
        method: 'POST',
        body: JSON.stringify({ trade_id: tradeId, price_cents: priceCents }),
      }
    ),
};
