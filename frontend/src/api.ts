// Typed client for the research backend.

export type Bias = 'bull' | 'bear' | 'neutral'
export interface ConditionMeta { id: string; label: string; category: string; bias: Bias; description: string }
export interface Meta {
  symbols: { id: string; label: string }[]
  timeframes: string[]
  targets: number[]
  default_stop_atr: number
  default_horizon: Record<string, number>
  sessions: string[]
  conditions: ConditionMeta[]
}

export interface Bars {
  symbol: string; tf: string; first_index: number; total: number
  time: number[]; open: number[]; high: number[]; low: number[]; close: number[]; volume: number[]; atr: (number | null)[]
  levels: Record<string, (number | null)[]>
}

export interface Zone { kind: string; start: number; formed: number; end: number | null; top: number; bottom: number; status: string }
export interface Swing { i: number; conf: number; price: number; kind: string; side: 'high' | 'low' }
export interface StructEvent { i: number; kind: string; level: number; ref_i: number }
export interface Pool { kind: 'eqh' | 'eql'; start: number; end: number | null; level: number }
export interface Overlays { zones: Zone[]; swings: Swing[]; events: StructEvent[]; pools: Pool[] }

export interface Level { price: number; kind: string; rr?: number }
export interface QuickStats {
  n: number; win_rate: number | null; avg_r: number | null; profit_factor: number | null
  oos_n: number; oos_avg_r: number | null; grade: string; score: number
  mfe_med_atr: number | null; mfe_p75_atr: number | null
}
export interface TradeZone {
  kind: string; label: string; direction: 'long' | 'short'; top: number; bottom: number; start_i: number
  entry: number; stop: number; fresh: boolean; distance_atr: number | null
  targets: Level[]; stat_targets: Level[]; confluence: string[]; stats: QuickStats; setup_condition: string
}
export interface KeyZones {
  index: number; time: number; close: number; atr: number
  buy_zones: TradeZone[]; sell_zones: TradeZone[]
  liquidity: { above: Level[]; below: Level[] }
  disclaimer: string
}

export interface Summary {
  n: number; wins?: number; losses?: number; timeouts?: number
  win_rate?: number; win_rate_lb?: number; breakeven_win_rate?: number
  avg_r?: number; median_r?: number; total_r?: number; profit_factor?: number | null
  max_dd_r?: number; max_losing_streak?: number; baseline_r?: number; edge_r?: number
  t_stat?: number; p_value?: number; shrunk_r?: number; trades_per_year?: number | null
  avg_r_ci?: [number, number]; avg_mfe_atr?: number; avg_mae_atr?: number; avg_bars?: number
}
export interface BreakdownRow { key: string; n: number; win_rate: number; avg_r: number; total_r: number }
export interface FilterRow { id: string; label: string; n: number; avg_r: number; win_rate: number; shrunk_r: number; delta: number }
export interface Example { i: number; time: number; r: number; outcome: 'win' | 'loss' | 'timeout'; mfe_atr: number; bars: number; session: string; oos: boolean }
export interface TestResult {
  symbol: string; tf: string; direction: string; conditions: string[]; condition_labels: string[]
  params: { target_r: number; stop_atr: number; horizon: number; cooldown: number; include_costs: boolean }
  split_time: number; data_range: [number, number]
  stats: { all: Summary; in_sample: Summary; out_of_sample: Summary }
  evidence: { score: number; grade: string; reasons: string[] }
  equity?: { time: number; value: number; oos: boolean }[]
  rolling?: { time: number; value: number }[]
  breakdowns?: Record<string, BreakdownRow[]>
  decay?: { status: string; message: string; recent_avg_r?: number; earlier_avg_r?: number; recent_n?: number }
  best_worst?: { best: FilterRow[]; worst: FilterRow[] }
  examples?: Example[]
}
export interface TestRequest {
  symbol: string; tf: string; conditions: string[]; direction: 'long' | 'short'
  target_r: number; stop_atr: number; cooldown: number; date_from?: string | null; date_to?: string | null
  include_costs: boolean
}

export interface InventoryRow { symbol: string; tf: string; bars: number; start: string | null; end: string | null }
export interface Status {
  mt5: { connected: boolean; error?: string; server?: string; company?: string; symbols?: Record<string, string | null>; time_mode?: string }
  inventory: InventoryRow[]
  sync: { running: boolean; log: string[]; progress: number; total: number }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init)
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try { const j = await r.json(); if (j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch { /* not json */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}
const post = <T,>(path: string, body: unknown) =>
  req<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
const qs = (o: Record<string, string | number | undefined | null>) =>
  Object.entries(o).filter(([, v]) => v !== undefined && v !== null).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')

export const api = {
  meta: () => req<Meta>('/api/meta'),
  status: () => req<Status>('/api/status'),
  connect: () => post<Status['mt5']>('/api/mt5/connect', {}),
  sync: (body: { symbols?: string[]; timeframes?: string[]; start?: string | null }) => post<{ started: boolean }>('/api/sync', body),
  bars: (symbol: string, tf: string, count: number, end_index?: number) => req<Bars>(`/api/bars?${qs({ symbol, tf, count, end_index })}`),
  indexOf: (symbol: string, tf: string, time: number) => req<{ index: number; total: number }>(`/api/index_of?${qs({ symbol, tf, time })}`),
  overlays: (symbol: string, tf: string, from_i: number, to_i: number) => req<Overlays>(`/api/overlays?${qs({ symbol, tf, from_i, to_i })}`),
  zoneStats: (symbol: string, tf: string) => req<Record<string, QuickStats>>(`/api/zone_stats?${qs({ symbol, tf })}`),
  zones: (symbol: string, tf: string, at_index?: number) => req<KeyZones>(`/api/zones?${qs({ symbol, tf, at_index })}`),
  barContext: (symbol: string, tf: string, index: number) =>
    req<{ index: number; time: number; conditions: { id: string; label: string; category: string; bias: Bias }[] }>(`/api/bar_context?${qs({ symbol, tf, index })}`),
  test: (body: TestRequest) => post<TestResult>('/api/research/test', body),
  setupView: (s: SetupRef & { symbol: string; tf: string }) => s.watch_id !== undefined
    ? req<SetupView>(`/api/live/watchlist/${s.watch_id}/view`)
    : req<SetupView>(`/api/setup_view?${qs({ symbol: s.symbol, tf: s.tf, conditions: s.conditions.join(','), direction: s.direction,
      target_r: s.target_r, stop_atr: s.stop_atr, cooldown: s.cooldown })}`),
}

/** A setup (watched or discovered) to draw on the chart. */
export interface SetupRef { conditions: string[]; direction: 'long' | 'short' | 'both'; name: string; watch_id?: number; target_r?: number; stop_atr?: number; cooldown?: number }
export interface SetupView {
  symbol: string; tf: string; direction: 'long' | 'short' | 'both'; index: number; time: number; close: number; atr: number
  checklist: { id: string; label: string; met: boolean; bias: Bias; level: number | null; level_label: string | null
    zone: { top: number; bottom: number; start_time: number; label: string } | null }[]
  met: number; total: number
  occurrences: { i: number; time: number; r: number; outcome: string; oos: boolean; direction?: 'long' | 'short' }[]
  stats: Summary; evidence: Evidence
  entry_zone: { available: false; reason: string } | {
    available: true; top: number; bottom: number; mid: number; stop: number; target: number
    stop_atr: number | string; target_r: number | string; anchor: string; basis: string; n: number | null }
}

// ------------------------------------------------------------------ live
export interface Evidence { score: number; grade: string; reasons: string[] }
export interface Decay { status: string; message: string; recent_avg_r?: number; earlier_avg_r?: number; recent_n?: number }
export interface WatchStats {
  stats: { all: Summary; in_sample: Summary; out_of_sample: Summary }
  evidence: Evidence; decay: Decay | null; split_time: number; as_of: number
}
export interface Watch {
  id: number; created: number; name: string; symbol: string; tf: string; direction: 'long' | 'short' | 'both'
  conditions: string[]; target_r: number; stop_atr: number; cooldown: number; active: number; plan?: string | null
  stats: WatchStats | null; stats_updated: number
  record?: { signals: number; open: number; wins: number; losses: number; avg_r: number | null; total_r: number }
}
export interface SignalPayload {
  name: string; conditions: string[]; condition_labels: string[]; target_r: number; stop_atr: number | null; atr: number
  strategy?: string; stop_text?: string; target_text?: string; unproven?: boolean
  liquidity_targets: Level[]; context: { id: string; label: string; category: string; bias: Bias }[]
  stats: WatchStats['stats'] | null; evidence: Evidence | null; decay: Decay | null; stats_as_of: number | null
  similar: { k: number; of: number; win_rate: number; avg_r: number; all_avg_r: number; mean_similarity: number;
    examples: { time: number; r: number; similarity: number }[] } | null
}
export interface Signal {
  id: number; watch_id: number; symbol: string; tf: string; direction: 'long' | 'short'; bar_time: number; detected_at: number
  entry: number; stop: number; target: number; payload: SignalPayload
  status: 'open' | 'win' | 'loss' | 'timeout'; result_r: number | null; closed_at: number | null
}
export interface LiveStatus {
  enabled: boolean; mt5_ok: boolean; mt5_error: string | null; last_tick: number | null; refreshing: boolean
  last_bars: { symbol: string; tf: string; time: number }[]; telegram: boolean; log: string[]
}
export interface PositionSize {
  symbol: string; direction: 'long' | 'short'; risk: number; lots: number; loss_at_stop: number; spread: number; spread_cost: number
  profit_at_target?: number; rr?: number; note: string | null; min_lot_risk: number; risk_pct_of_balance?: number
  account: { balance: number; equity: number; currency: string; leverage: number } | null
}
export interface TelegramCfg { configured: boolean; has_token: boolean; token_hint: string; chat_id: number | null; enabled: boolean }
export interface WatchRequest {
  symbol: string; tf: string; conditions: string[]; direction: 'long' | 'short'
  target_r: number; stop_atr: number; cooldown: number; name?: string; plan?: string
}

const send = <T,>(method: string, path: string, body?: unknown) =>
  req<T>(path, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })

export const live = {
  status: () => req<LiveStatus>('/api/live/status'),
  toggle: (enabled: boolean) => post<{ enabled: boolean }>('/api/live/toggle', { enabled }),
  scanNow: () => post<{ new_signals: number }>('/api/live/scan_now', {}),
  watchlist: () => req<Watch[]>('/api/live/watchlist'),
  addWatch: (w: WatchRequest) => post<Watch>('/api/live/watchlist', w),
  patchWatch: (id: number, p: { active?: boolean; name?: string }) => send<Watch>('PATCH', `/api/live/watchlist/${id}`, p),
  refreshWatch: (id: number) => post<Watch>(`/api/live/watchlist/${id}/refresh`, {}),
  deleteWatch: (id: number) => send<{ deleted: number }>('DELETE', `/api/live/watchlist/${id}`),
  signals: (since_id = 0, limit = 200) => req<Signal[]>(`/api/live/signals?${qs({ since_id, limit })}`),
  telegram: () => req<TelegramCfg>('/api/live/telegram'),
  setTelegram: (p: { token?: string; chat_id?: number; enabled?: boolean }) => post<TelegramCfg>('/api/live/telegram', p),
  detectTelegram: () => post<TelegramCfg>('/api/live/telegram/detect', {}),
  testTelegram: () => post<{ sent: boolean }>('/api/live/telegram/test', {}),
  positionSize: (q: { symbol: string; entry: number; stop: number; target?: number | null; risk?: number; risk_pct?: number }) =>
    req<PositionSize>(`/api/live/position_size?${qs(q)}`),
  candle: (symbol: string, tf: string) =>
    req<{ time: number; open: number; high: number; low: number; close: number; bid: number | null; closes_at: number }>(`/api/live/candle?${qs({ symbol, tf })}`),
}

// ------------------------------------------------------------------ discovery
export interface PatternRow {
  id: number; run_id: number; created: number; symbol: string; tf: string; direction: 'long' | 'short'
  target_r: number; stop_atr: number; cooldown: number; conditions: string[]; source: string
  status: 'validated' | 'promising' | 'failed'; score: number; grade: string; q_oos: number; stability: number | null
  n_all: number; avg_r: number | null; win_rate: number | null; oos_n: number; oos_avg_r: number | null; profit_factor: number | null
}
export interface CompareRow { symbol: string; tf: string; n?: number; win_rate?: number; avg_r?: number; edge_r?: number; oos_n?: number; oos_avg_r?: number; grade?: string; error?: string }
export interface PatternDetail extends PatternRow {
  data: {
    labels: string[]; description: string; evidence: Evidence; is: Summary; oos: Summary; all: Summary
    p_oos: number; extra: { n_tested: number; split_time: number; min_n: number; cluster_profile?: Record<string, { cluster: number; overall: number }>; cluster_share?: number }
  }
  compare: { status: string; symbols: CompareRow[]; timeframes: CompareRow[] } | null
}
export interface Run {
  id: number; created: number; status: string; progress: number; total: number; message: string | null; finished: number | null
  params: { symbols: string[]; tfs: string[]; directions: string[]; target_r: number }
  summary: { validated: number; promising: number; failed: number; tested: number } | null
}
export interface RareEvent {
  kind: string; labels: string[]; conditions: string[]; freq: number; n: number
  fwd_mean_atr: number | null; fwd_median_atr: number | null; up_pct: number | null
  max_up_atr: number | null; max_down_atr: number | null; long_avg_r: number | null; short_avg_r: number | null
  examples: { time: number; fwd_atr: number }[]
}

export const discovery = {
  start: (b: { symbols: string[]; tfs: string[]; directions: string[]; target_r: number; stop_atr: number; min_n?: number | null }) =>
    post<{ run_id: number }>('/api/discovery/runs', b),
  runs: () => req<{ runs: Run[]; worker: { current: string | null; queued: number; log: string[] }; counts: Record<string, number> }>('/api/discovery/runs'),
  cancel: (id: number) => post<{ cancelled: number }>(`/api/discovery/runs/${id}/cancel`, {}),
  patterns: (f: { symbol?: string; tf?: string; direction?: string; status?: string; min_n?: number }) =>
    req<PatternRow[]>(`/api/discovery/patterns?${qs(f)}`),
  pattern: (id: number) => req<PatternDetail>(`/api/discovery/patterns/${id}`),
  compare: (id: number) => post<{ queued: number }>(`/api/discovery/patterns/${id}/compare`, {}),
  rare: (symbol: string, tf: string) =>
    req<{ symbol: string; tf: string; time: number; horizon: number; active_conditions: number; events: RareEvent[] }>(`/api/discovery/rare?${qs({ symbol, tf })}`),
}

// ------------------------------------------------------------------ execution
export interface PlanSide { signals: number; taken: number; per_signal_r: number | null; win_rate: number | null; avg_r?: number; max_dd_r?: number; profit_factor?: number | null }
export interface PlanResult { key: string; plan: { entry: string; stop: string; target: string; manage: string }; description: string; is: PlanSide; oos: PlanSide; is_score: number | null }
export interface OptimizeResult {
  symbol: string; tf: string; ltf: string | null; direction: string; conditions: string[]; labels: string[]
  occurrences: number; split_time: number; baseline: PlanResult; best: PlanResult; verdict: string; ranked: PlanResult[]; n_plans: number
  recommendation: { key: string; description: string; reason: string }
}
export interface StudyPlan { key: string; description: string; setups: number; mean_d_oos: number; median_d_oos: number; share_improved: number; mean_d_is: number; mean_d_winrate: number; robust: boolean }
export interface StudyResult { finished: number; setups: number; plans: StudyPlan[]; components: Record<string, Record<string, number>>; robust: StudyPlan[]; rule: string }

export const execution = {
  optimize: (b: { symbol: string; tf: string; conditions: string[]; direction: string; cooldown: number }) => post<OptimizeResult>('/api/execution/optimize', b),
  startStudy: () => post<{ started: boolean }>('/api/execution/study', {}),
  study: () => req<{ state: { running: boolean; progress: number; total: number; message: string }; result: StudyResult | null }>('/api/execution/study'),
}
