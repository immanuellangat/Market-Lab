import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, type Meta, type SetupRef } from './api'

export type Page = 'chart' | 'research' | 'patterns' | 'scanner' | 'forward' | 'portfolio' | 'notifications' | 'data' | 'settings'

/** A request to open the chart at a moment in history (optionally in replay). */
export interface ChartFocus { symbol: string; tf: string; time: number; replay: boolean; label?: string; direction?: string; setup?: SetupRef }

interface AppState {
  meta: Meta | null
  metaError: string | null
  page: Page
  setPage: (p: Page) => void
  symbol: string
  setSymbol: (s: string) => void
  tf: string
  setTf: (t: string) => void
  focus: ChartFocus | null
  openChartAt: (f: ChartFocus) => void
  clearFocus: () => void
}

const Ctx = createContext<AppState | null>(null)

function stored(key: string, fallback: string) {
  try { return localStorage.getItem(key) ?? fallback } catch { return fallback }
}
function store(key: string, v: string) {
  try { localStorage.setItem(key, v) } catch { /* storage unavailable */ }
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [metaError, setMetaError] = useState<string | null>(null)
  const [page, setPageS] = useState<Page>(() => stored('page', 'chart') as Page)
  const [symbol, setSymbolS] = useState(() => stored('symbol', 'EURUSD'))
  const [tf, setTfS] = useState(() => stored('tf', 'H1'))
  const [focus, setFocus] = useState<ChartFocus | null>(null)

  useEffect(() => {
    api.meta().then(setMeta).catch(e => setMetaError(String(e.message ?? e)))
  }, [])

  const setPage = (p: Page) => { setPageS(p); store('page', p) }
  const setSymbol = (s: string) => { setSymbolS(s); store('symbol', s) }
  const setTf = (t: string) => { setTfS(t); store('tf', t) }
  const openChartAt = (f: ChartFocus) => { setSymbol(f.symbol); setTf(f.tf); setFocus(f); setPage('chart') }

  return (
    <Ctx.Provider value={{ meta, metaError, page, setPage, symbol, setSymbol, tf, setTf, focus, openChartAt, clearFocus: () => setFocus(null) }}>
      {children}
    </Ctx.Provider>
  )
}

export function useApp() {
  const c = useContext(Ctx)
  if (!c) throw new Error('useApp outside provider')
  return c
}

/** Local timezone offset in seconds; charts display local time like TradingView does. */
export const TZ_OFFSET = -new Date().getTimezoneOffset() * 60
export const TZ_LABEL = (() => {
  const h = TZ_OFFSET / 3600
  return h === 0 ? 'UTC' : `UTC${h > 0 ? '+' : '-'}${Math.abs(h)}`
})()

export const fmt = {
  r: (x: number | null | undefined, d = 2) => (x === null || x === undefined || !isFinite(x) ? '—' : `${x >= 0 ? '+' : ''}${x.toFixed(d)}R`),
  pct: (x: number | null | undefined, d = 1) => (x === null || x === undefined || !isFinite(x) ? '—' : `${(x * 100).toFixed(d)}%`),
  num: (x: number | null | undefined, d = 2) => (x === null || x === undefined || !isFinite(x) ? '—' : x.toFixed(d)),
  int: (x: number | null | undefined) => (x === null || x === undefined ? '—' : Math.round(x).toLocaleString()),
  price: (x: number | null | undefined) => {
    if (x === null || x === undefined || !isFinite(x)) return '—'
    const d = x >= 1000 ? 2 : x >= 50 ? 3 : 5
    return x.toFixed(d)
  },
  date: (t: number) => new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' '),
  local: (t: number) => new Date((t + TZ_OFFSET) * 1000).toISOString().slice(0, 16).replace('T', ' '),
  day: (t: number) => new Date(t * 1000).toISOString().slice(0, 10),
}
