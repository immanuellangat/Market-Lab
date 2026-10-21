import { useCallback, useEffect, useState } from 'react'
import { live, type Notice, type NoticeKind } from '../api'
import { fmt, useApp } from '../state'

const KINDS: { id: NoticeKind | 'all'; label: string }[] = [
  { id: 'all', label: 'All' }, { id: 'signal', label: 'Signals' }, { id: 'price_alert', label: 'Price alerts' },
  { id: 'edge', label: 'Edge changes' }, { id: 'system', label: 'System' },
]
const ICON: Record<string, string> = { signal: '📈', price_alert: '🔔', edge: '📉', system: '⚙' }
const PAGE = 100

function ago(t: number) {
  const s = Date.now() / 1000 - t
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}

export default function NotificationsPage({ onCount }: { onCount: (n: number) => void }) {
  const { openChartAt, setPage } = useApp()
  const [kind, setKind] = useState<NoticeKind | 'all'>('all')
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [items, setItems] = useState<Notice[]>([])
  const [counts, setCounts] = useState<Record<string, { total: number; unread: number }>>({})
  const [more, setMore] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await live.notifications({ kind: kind === 'all' ? undefined : kind, unread: unreadOnly, limit: PAGE })
      setItems(r.items); setCounts(r.by_kind); setMore(r.items.length === PAGE); onCount(r.unread); setErr(null)
    } catch (e) { setErr((e as Error).message) }
  }, [kind, unreadOnly, onCount])
  useEffect(() => { load(); const h = setInterval(load, 15000); return () => clearInterval(h) }, [load])

  const loadMore = async () => {
    const last = items[items.length - 1]
    if (!last) return
    const r = await live.notifications({ kind: kind === 'all' ? undefined : kind, unread: unreadOnly, limit: PAGE, before_id: last.id })
    setItems(x => [...x, ...r.items]); setMore(r.items.length === PAGE)
  }
  const mark = async (ids: number[] | null, read = true) => {
    const c = await live.markNotifications(ids, read)
    onCount(c.unread)
    setItems(x => x.map(n => (ids === null || ids.includes(n.id) ? { ...n, read: read ? 1 : 0 } : n)))
    setCounts(c.by_kind)
  }
  const open = (n: Notice) => {
    if (!n.read) mark([n.id])
    const l = n.link
    if (l?.page === 'chart' && l.symbol && l.tf && l.time) openChartAt({ symbol: l.symbol, tf: l.tf, time: l.time, replay: false, label: n.title })
    else if (l?.page) setPage(l.page as never)
  }

  const unreadTotal = Object.values(counts).reduce((s, c) => s + c.unread, 0)

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Notifications</h1>
          <p>Everything the app has told you: trade signals, price alerts, setups whose edge is changing and connection problems.</p>
        </div>
        <button className="btn" disabled={!unreadTotal} onClick={() => mark(null)}>Mark all read</button>
      </div>

      <div className="toolbar">
        <div className="seg" role="group" aria-label="Type">
          {KINDS.map(k => {
            const c = k.id === 'all' ? unreadTotal : counts[k.id]?.unread ?? 0
            return <button key={k.id} className={kind === k.id ? 'on' : ''} onClick={() => setKind(k.id)}>{k.label}{c ? ` (${c})` : ''}</button>
          })}
        </div>
        <label className="row small" style={{ gap: 6, cursor: 'pointer' }}>
          <input type="checkbox" checked={unreadOnly} onChange={e => setUnreadOnly(e.target.checked)} /> Unread only
        </label>
      </div>

      {err && <div className="error">{err}</div>}

      <div className="panel">
        <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: 6 }}>
          {items.length === 0 && <div className="muted small" style={{ padding: 10 }}>
            {unreadOnly ? 'Nothing unread.' : 'No notifications yet. Signals, price alerts, edge changes and connection problems will appear here.'}
          </div>}
          {items.map(n => (
            <div key={n.id} className={`notice ${n.read ? '' : 'unread'} ${n.level}`} onClick={() => open(n)}
              title={n.link ? 'Open' : undefined} style={{ cursor: n.link ? 'pointer' : 'default' }}>
              <span className="notice-icon" aria-hidden>{ICON[n.kind] ?? '•'}</span>
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="row" style={{ gap: 8 }}>
                  <b className="grow" style={{ fontWeight: n.read ? 500 : 650 }}>{n.title}</b>
                  <span className="small faint" title={fmt.date(n.created) + ' UTC'} style={{ whiteSpace: 'nowrap' }}>{ago(n.created)}</span>
                </div>
                {n.body && <div className="small muted">{n.body}</div>}
              </div>
              <button className="btn sm ghost" title={n.read ? 'Mark unread' : 'Mark read'} aria-label={n.read ? 'Mark unread' : 'Mark read'}
                onClick={e => { e.stopPropagation(); mark([n.id], !n.read) }}>{n.read ? '○' : '●'}</button>
            </div>
          ))}
          {more && <button className="btn sm" style={{ alignSelf: 'center', margin: 8 }} onClick={loadMore}>Load older</button>}
        </div>
      </div>
      <div className="small faint">Telegram gets signals, price alerts, edges that weaken and MT5 outages; this page keeps all of them.</div>
    </div>
  )
}
