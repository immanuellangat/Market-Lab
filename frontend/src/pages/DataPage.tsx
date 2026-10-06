import { useCallback, useEffect, useState } from 'react'
import { api, type Status } from '../api'
import { fmt, useApp } from '../state'

export default function DataPage() {
  const { meta } = useApp()
  const [st, setSt] = useState<Status | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [start, setStart] = useState('2015-01-01')

  const refresh = useCallback(() => api.status().then(setSt).catch(e => setErr(String(e.message ?? e))), [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    if (!st?.sync.running) return
    const h = setInterval(refresh, 1500)
    return () => clearInterval(h)
  }, [st?.sync.running, refresh])

  const connect = async () => {
    setConnecting(true); setErr(null)
    try { await api.connect(); await refresh() } catch (e) { setErr((e as Error).message) } finally { setConnecting(false) }
  }
  const sync = async (full: boolean) => {
    setErr(null)
    try { await api.sync({ start: full ? start : null }); await refresh() } catch (e) { setErr((e as Error).message) }
  }

  const mt5 = st?.mt5
  const tfs = meta?.timeframes ?? []
  const syms = meta?.symbols.map(s => s.id) ?? []
  const cell = (s: string, tf: string) => st?.inventory.find(r => r.symbol === s && r.tf === tf)

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Data</h1>
          <p>Historical bars come from your MetaTrader 5 terminal (JustMarkets). Times are converted to UTC; only closed candles are stored.</p>
        </div>
      </div>
      {err && <div className="error">{err}</div>}

      <div className="grid2">
        <div className="panel">
          <div className="panel-head"><h3>MetaTrader 5 connection</h3>
            <span className={`chip ${mt5?.connected ? 'bull' : 'bear'}`}>{mt5?.connected ? 'connected' : 'not connected'}</span></div>
          <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {mt5?.connected ? (
              <div className="small">
                <div>Server: <b>{mt5.server}</b> · {mt5.company}</div>
                <div className="muted">Server clock mode: {mt5.time_mode === 'ny+7' ? 'New York + 7h (DST-aware)' : 'fixed offset'}</div>
                <div className="row" style={{ marginTop: 8 }}>
                  {Object.entries(mt5.symbols ?? {}).map(([k, v]) => <span key={k} className={`chip ${v ? '' : 'bear'}`}>{k} → {v ?? 'not found'}</span>)}
                </div>
              </div>
            ) : (
              <div className="small muted">
                {mt5?.error && <div className="error" style={{ marginBottom: 8 }}>{mt5.error}</div>}
                <ol style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6 }}>
                  <li>Open MetaTrader 5 and log in to your JustMarkets account (tick “Save password”).</li>
                  <li>Tools → Options → Charts → set “Max bars in chart” to <b>Unlimited</b> for deep history.</li>
                  <li>Click Connect. Your password stays in MT5 — this app never sees it.</li>
                </ol>
              </div>
            )}
            <div className="row"><button className="btn primary" onClick={connect} disabled={connecting}>{connecting && <span className="spinner" />} Connect to MT5</button></div>
          </div>
        </div>

        <div className="panel">
          <div className="panel-head"><h3>Download history</h3></div>
          <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div className="row">
              <span className="small muted">Start date</span>
              <input type="date" value={start} onChange={e => setStart(e.target.value)} />
              <button className="btn primary" disabled={st?.sync.running} onClick={() => sync(true)}>Download all</button>
              <button className="btn" disabled={st?.sync.running} onClick={() => sync(false)}>Update to latest</button>
            </div>
            <div className="small faint">Downloads all six instruments on M5, M15, H1, H4 and D1. The first full download can take several minutes.</div>
            {st?.sync && (st.sync.running || st.sync.log.length > 0) && (
              <>
                <div className="row small">
                  {st.sync.running && <span className="spinner" />}
                  <span className="num">{st.sync.progress} / {st.sync.total}</span>
                  <div className="bar-track grow"><div className="bar-fill" style={{ width: `${st.sync.total ? (st.sync.progress / st.sync.total) * 100 : 0}%`, background: 'var(--accent)' }} /></div>
                </div>
                <pre className="small num" style={{ maxHeight: 160, overflow: 'auto', margin: 0, background: 'var(--panel-2)', padding: 8, borderRadius: 6 }}>{st.sync.log.join('\n')}</pre>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h3>Stored history</h3><button className="btn sm" onClick={refresh}>Refresh</button></div>
        <div className="panel-body" style={{ overflowX: 'auto' }}>
          <table className="t num">
            <thead><tr><th>Instrument</th>{tfs.map(t => <th key={t} className="r">{t}</th>)}</tr></thead>
            <tbody>
              {syms.map(s => (
                <tr key={s}>
                  <td><b>{s}</b></td>
                  {tfs.map(t => {
                    const c = cell(s, t)
                    return <td key={t} className="r" title={c?.start ? `${c.start} → ${c.end}` : ''}>
                      {c?.bars ? <>{fmt.int(c.bars)}<div className="faint small">{c.start?.slice(0, 10)} →</div></> : <span className="faint">—</span>}
                    </td>
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
