import { useCallback, useEffect, useState } from 'react'
import { priceAlertTitle, signalTitle, usePriceAlertToasts, useSignalAlerts } from './alerts'
import ChartPage from './pages/ChartPage'
import DataPage from './pages/DataPage'
import LivePage from './pages/LivePage'
import ResearchPage from './pages/ResearchPage'
import PatternsPage from './pages/PatternsPage'
import PortfolioPage from './pages/PortfolioPage'
import ForwardPage from './pages/ForwardPage'
import NotificationsPage from './pages/NotificationsPage'
import SettingsPage from './pages/SettingsPage'
import { live } from './api'
import { AppProvider, fmt, useApp, type Page } from './state'

const ICONS: Record<Page, string> = {
  chart: 'M3 3v18h18M7 15l3-4 3 3 5-7',
  research: 'M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3',
  patterns: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',
  scanner: 'M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0M12 12l5-5M12 4v2',
  portfolio: 'M3 17l6-6 4 4 8-8M15 7h6v6',
  forward: 'M4 12h12M12 6l6 6-6 6M4 5v14',
  notifications: 'M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  data: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
}
const NAV: { id: Page; label: string; tag?: string }[] = [
  { id: 'chart', label: 'Chart explorer' },
  { id: 'research', label: 'Research lab' },
  { id: 'scanner', label: 'Live signals' },
  { id: 'patterns', label: 'Discovered patterns' },
  { id: 'forward', label: 'Forward tests' },
  { id: 'portfolio', label: 'Portfolio' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'data', label: 'Data' },
  { id: 'settings', label: 'Settings' },
]

function Shell() {
  const { page, setPage, metaError } = useApp()
  const { toasts, dismiss } = useSignalAlerts()
  const { paToasts, dismissPa } = usePriceAlertToasts()
  // unread notifications badge (the page itself also reports its count when it changes)
  const [unread, setUnread] = useState(0)
  const onCount = useCallback((n: number) => setUnread(n), [])
  useEffect(() => {
    const poll = () => live.notificationCount().then(c => setUnread(c.unread)).catch(() => {})
    poll()
    const h = setInterval(poll, 15000)
    return () => clearInterval(h)
  }, [])
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem('theme') ?? 'dark' } catch { return 'dark' } })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('theme', theme) } catch { /* ignore */ }
  }, [theme])

  return (
    <div className="app">
      <nav className="nav">
        <div className="brand">
          <div className="brand-mark">ML</div>
          <div><div className="brand-name">Market Lab</div><div className="brand-sub">price-action research</div></div>
        </div>
        {NAV.map(n => (
          <button key={n.id} className={`nav-item ${page === n.id ? 'active' : ''}`} onClick={() => setPage(n.id)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={ICONS[n.id]} /></svg>
            {n.label}{n.tag && <span className="nav-tag">{n.tag}</span>}
            {n.id === 'notifications' && unread > 0 && <span className="nav-badge" aria-label={`${unread} unread`}>{unread > 99 ? '99+' : unread}</span>}
          </button>
        ))}
        <div className="nav-foot">
          <button className="btn sm" onClick={() => setTheme(t => (t === 'dark' ? 'light' : 'dark'))}>{theme === 'dark' ? '☀ Light' : '☾ Dark'} theme</button>
          <span>Historical evidence, not guarantees.</span>
        </div>
      </nav>
      <main className="main">
        {metaError && <div className="page"><div className="error">Backend not reachable: {metaError}. Start it with <code>start.bat</code>.</div></div>}
        {page === 'chart' && <ChartPage theme={theme} />}
        {page === 'research' && <ResearchPage theme={theme} />}
        {page === 'scanner' && <LivePage />}
        {page === 'patterns' && <PatternsPage theme={theme} />}
        {page === 'portfolio' && <PortfolioPage theme={theme} />}
        {page === 'forward' && <ForwardPage />}
        {page === 'notifications' && <NotificationsPage onCount={onCount} />}
        {page === 'data' && <DataPage />}
        {page === 'settings' && <SettingsPage />}
      </main>
      <div className="toasts">
        {paToasts.map(a => (
          <div key={`pa${a.id}`} className="toast" onClick={() => { setPage('chart'); dismissPa(a.id) }}>
            <div className="row">
              <b className="grow">{priceAlertTitle(a)}</b>
              <button className="btn sm ghost" aria-label="Dismiss" onClick={e => { e.stopPropagation(); dismissPa(a.id) }}>✕</button>
            </div>
            <div className="small num muted">
              Price ≈ {fmt.price(a.trigger_price)} · zone {a.top === a.bottom ? fmt.price(a.top) : `${fmt.price(a.bottom)}–${fmt.price(a.top)}`}{a.tf ? ` · drawn on ${a.tf}` : ''}
            </div>
          </div>
        ))}
        {toasts.map(s => (
          <div key={s.id} className={`toast ${s.direction}`} onClick={() => { setPage('scanner'); dismiss(s.id) }}>
            <div className="row">
              <b className="grow">{signalTitle(s)}</b>
              <button className="btn sm ghost" aria-label="Dismiss" onClick={e => { e.stopPropagation(); dismiss(s.id) }}>✕</button>
            </div>
            <div className="small num muted">
              Entry ≈ {fmt.price(s.entry)} · SL {fmt.price(s.stop)} · TP {fmt.price(s.target)}{s.payload.evidence ? ` · grade ${s.payload.evidence.grade}` : ''}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function App() {
  return <AppProvider><Shell /></AppProvider>
}
