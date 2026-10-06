import { useCallback, useEffect, useState } from 'react'
import { live, type LiveStatus, type Signal, type TelegramCfg, type Watch } from '../api'
import { notifyPermission, requestNotify } from '../alerts'
import { fmt, useApp } from '../state'

const ago = (t: number) => {
  const s = Math.max(0, Date.now() / 1000 - t)
  if (s < 90) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

export default function LivePage() {
  const [st, setSt] = useState<LiveStatus | null>(null)
  const [signals, setSignals] = useState<Signal[]>([])
  const [watches, setWatches] = useState<Watch[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'open' | 'closed'>('all')
  const [perm, setPerm] = useState(notifyPermission())
  const [busy, setBusy] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [s, sig, w] = await Promise.all([live.status(), live.signals(0, 200), live.watchlist()])
      setSt(s); setSignals(sig); setWatches(w); setErr(null)
    } catch (e) { setErr((e as Error).message) }
  }, [])
  useEffect(() => { refresh(); const h = setInterval(refresh, 15000); return () => clearInterval(h) }, [refresh])

  const act = async (label: string, f: () => Promise<unknown>) => {
    setBusy(label); setErr(null)
    try { await f(); await refresh() } catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }

  const shown = signals.filter(s => filter === 'all' || (filter === 'open' ? s.status === 'open' : s.status !== 'open'))
  const newest = st?.last_bars.reduce((m, b) => Math.max(m, b.time), 0) ?? 0

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Live signals</h1>
          <p>Watched setups are checked on every closed candle. A signal means conditions that historically carried an edge are present now — with the evidence attached.</p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-body row" style={{ gap: 14 }}>
          <span className={`chip ${st?.mt5_ok ? 'bull' : 'bear'}`}>{st?.mt5_ok ? 'MT5 connected' : 'MT5 offline'}</span>
          <span className={`chip ${st?.enabled ? 'bull' : 'warn'}`}>{st?.enabled ? 'live feed on' : 'live feed paused'}</span>
          {newest > 0 && <span className="small muted">latest candle closed {fmt.date(newest)} UTC</span>}
          {st?.refreshing && <span className="small faint row"><span className="spinner" /> updating history</span>}
          <span className="grow" />
          <button className="btn sm" disabled={!!busy} onClick={() => act('scan', () => live.scanNow())}>{busy === 'scan' && <span className="spinner" />} Check now</button>
          <button className="btn sm" onClick={() => act('toggle', () => live.toggle(!st?.enabled))}>{st?.enabled ? 'Pause feed' : 'Resume feed'}</button>
          {perm !== 'granted' && perm !== 'unsupported' && (
            <button className="btn sm primary" onClick={() => requestNotify().then(p => setPerm(p as NotificationPermission))}>Enable desktop alerts</button>
          )}
          {perm === 'granted' && <span className="chip accent">desktop alerts on</span>}
        </div>
        {st && !st.mt5_ok && st.mt5_error && <div className="panel-body small error" style={{ margin: '0 14px 12px' }}>{st.mt5_error}</div>}
      </div>

      {err && <div className="error">{err}</div>}

      <div className="live-grid">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
          <div className="row">
            <h3 style={{ fontSize: 14 }}>Signals</h3>
            <span className="grow" />
            <div className="seg">
              {(['all', 'open', 'closed'] as const).map(f => <button key={f} className={filter === f ? 'on' : ''} onClick={() => setFilter(f)}>{f}</button>)}
            </div>
          </div>
          {shown.length === 0 && (
            <div className="notice">
              {watches.length === 0
                ? <>No setups are being watched yet. Test a hypothesis in the <b>Research lab</b> and click <b>Watch this setup</b> — this page then fills with signals as candles close.</>
                : <>No signals yet. Your {watches.length} watched setup{watches.length > 1 ? 's are' : ' is'} checked on every closed candle; the first match will appear here, with a sound and desktop alert.</>}
            </div>
          )}
          {shown.map(s => <SignalCard key={s.id} s={s} />)}
        </div>

        <div className="side" style={{ overflow: 'visible' }}>
          <WatchlistPanel watches={watches} busy={busy} act={act} />
          <TelegramPanel />
          {st && st.log.length > 0 && (
            <div className="panel">
              <div className="panel-head"><h3>Activity</h3></div>
              <pre className="panel-body small num" style={{ margin: 0, maxHeight: 220, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{st.log.join('\n')}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function SignalCard({ s }: { s: Signal }) {
  const { openChartAt } = useApp()
  const [open, setOpen] = useState(false)
  const p = s.payload
  const a = p.stats?.all
  const o = p.stats?.out_of_sample
  const long = s.direction === 'long'
  const statusCls = s.status === 'win' ? 'bull' : s.status === 'loss' ? 'bear' : s.status === 'timeout' ? '' : 'accent'
  return (
    <div className="panel signal-card">
      <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div className="row">
          <span className={`chip ${long ? 'bull' : 'bear'}`} style={{ fontWeight: 600 }}>{long ? 'BUY' : 'SELL'}</span>
          <b>{s.symbol} {s.tf}</b>
          <span className="muted">{p.name}</span>
          <span className="grow" />
          <span className={`chip ${statusCls}`}>{s.status === 'open' ? 'open' : `${s.status} ${fmt.r(s.result_r)}`}</span>
          <span className="small faint" title={fmt.date(s.bar_time) + ' UTC'}>{ago(s.detected_at)}</span>
        </div>

        <div className="levels num">
          <div><span className="muted">Entry ≈</span> {fmt.price(s.entry)}<div className="faint small">next candle open</div></div>
          <div><span className="muted">Stop</span> <span className="neg">{fmt.price(s.stop)}</span><div className="faint small">{p.stop_atr}× ATR</div></div>
          <div><span className="muted">Target</span> <span className="pos">{fmt.price(s.target)}</span><div className="faint small">{p.target_r}R</div></div>
          {p.liquidity_targets.slice(0, 2).map((t, k) => (
            <div key={k}><span className="muted">Liquidity</span> {fmt.price(t.price)}<div className="faint small">{t.kind} · {fmt.num(t.rr, 1)}R</div></div>
          ))}
        </div>

        <div className="row small">
          <span className="muted">Triggered by:</span>
          {p.condition_labels.map(l => <span key={l} className="chip accent">{l}</span>)}
        </div>

        <div className="evidence-row">
          {p.evidence && <div className={`grade sm ${p.evidence.grade}`}>{p.evidence.grade}</div>}
          <div className="small" style={{ lineHeight: 1.6 }}>
            {a && a.n > 0 ? (
              <>History: <b className="num">{fmt.int(a.n)}</b> past occurrences · win <b className="num">{fmt.pct(a.win_rate, 0)}</b> · expectancy <b className={`num ${(a.avg_r ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmt.r(a.avg_r)}</b> per trade · out-of-sample <span className="num">{fmt.r(o?.avg_r)}</span> ({fmt.int(o?.n)} trades)</>
            ) : <span className="muted">No historical statistics available.</span>}
            {p.similar && (
              <div>Closest <b className="num">{p.similar.k}</b> of {p.similar.of} past matches (most similar context): win <b className="num">{fmt.pct(p.similar.win_rate, 0)}</b>, <b className={`num ${p.similar.avg_r >= 0 ? 'pos' : 'neg'}`}>{fmt.r(p.similar.avg_r)}</b> vs {fmt.r(p.similar.all_avg_r)} overall</div>
            )}
            {p.decay && (p.decay.status === 'weakening' || p.decay.status === 'broken') && <div className="neg">⚠ Edge {p.decay.status}: {p.decay.message}</div>}
          </div>
        </div>

        <div className="row">
          <button className="btn sm" onClick={() => openChartAt({ symbol: s.symbol, tf: s.tf, time: s.bar_time, replay: false, label: 'Signal', direction: s.direction })}>Open on chart</button>
          <button className="btn sm ghost" onClick={() => setOpen(v => !v)}>{open ? 'Hide details' : 'Market context & past matches'}</button>
          <span className="grow" />
          <span className="small faint">Historical evidence, not a guarantee.</span>
        </div>

        {open && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div>
              <div className="small muted" style={{ marginBottom: 6 }}>All conditions true on the signal candle ({p.context.length})</div>
              <div className="row">{p.context.map(c => <span key={c.id} className={`chip ${c.bias === 'bull' ? 'bull' : c.bias === 'bear' ? 'bear' : ''}`}>{c.label}</span>)}</div>
            </div>
            {p.similar && p.similar.examples.length > 0 && (
              <table className="t num">
                <thead><tr><th>Most similar past occurrence</th><th className="r">Similarity</th><th className="r">Result</th><th /></tr></thead>
                <tbody>
                  {p.similar.examples.map(e => (
                    <tr key={e.time}>
                      <td>{fmt.date(e.time)}</td><td className="r">{fmt.pct(e.similarity, 0)}</td>
                      <td className={`r ${e.r >= 0 ? 'pos' : 'neg'}`}>{fmt.r(e.r)}</td>
                      <td className="r"><button className="btn sm" onClick={() => openChartAt({ symbol: s.symbol, tf: s.tf, time: e.time, replay: true, label: 'Past match', direction: s.direction })}>Replay</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function WatchlistPanel({ watches, busy, act }: { watches: Watch[]; busy: string | null; act: (l: string, f: () => Promise<unknown>) => void }) {
  return (
    <div className="panel">
      <div className="panel-head"><h3>Watched setups</h3><span className="small faint">{watches.filter(w => w.active).length} active</span></div>
      <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {watches.length === 0 && <span className="small muted">Add setups from the Research lab.</span>}
        {watches.map(w => {
          const ev = w.stats?.evidence
          const rec = w.record
          return (
            <div key={w.id} className="zone-card" style={{ cursor: 'default', opacity: w.active ? 1 : 0.55 }}>
              <div className="row">
                <span className={`chip ${w.direction === 'long' ? 'bull' : 'bear'}`}>{w.direction === 'long' ? 'BUY' : 'SELL'}</span>
                <b style={{ fontSize: 13 }}>{w.symbol} {w.tf}</b>
                <span className="grow" />
                {ev && <span className="chip">Grade {ev.grade}</span>}
              </div>
              <div className="small muted">{w.name}</div>
              {w.stats?.decay && (w.stats.decay.status === 'weakening' || w.stats.decay.status === 'broken') && (
                <div className="small neg">⚠ edge {w.stats.decay.status}</div>
              )}
              {rec && <div className="small num">Live: {rec.signals} signals · {rec.wins}W / {rec.losses}L · {rec.open} open{rec.avg_r !== null ? ` · ${fmt.r(rec.avg_r)} avg` : ''}</div>}
              <div className="row">
                <button className="btn sm" onClick={() => act(`t${w.id}`, () => live.patchWatch(w.id, { active: !w.active }))}>{w.active ? 'Pause' : 'Resume'}</button>
                <button className="btn sm" disabled={!!busy} onClick={() => act(`r${w.id}`, () => live.refreshWatch(w.id))}>{busy === `r${w.id}` && <span className="spinner" />} Refresh stats</button>
                <span className="grow" />
                <button className="btn sm ghost" onClick={() => { if (confirm(`Stop watching "${w.name}"?`)) act(`d${w.id}`, () => live.deleteWatch(w.id)) }}>Remove</button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function TelegramPanel() {
  const [cfg, setCfg] = useState<TelegramCfg | null>(null)
  const [token, setToken] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => { live.telegram().then(setCfg).catch(() => {}) }, [])
  const run = async (f: () => Promise<TelegramCfg | unknown>, ok: string) => {
    setMsg(null)
    try { const r = await f(); if (r && typeof r === 'object' && 'configured' in r) setCfg(r as TelegramCfg); setMsg({ ok: true, text: ok }) }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }) }
  }
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Phone alerts (Telegram)</h3>
        <span className={`chip ${cfg?.configured ? 'bull' : ''}`}>{cfg?.configured ? 'connected' : 'not set up'}</span>
      </div>
      <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {!open && <button className="btn sm" onClick={() => setOpen(true)}>{cfg?.configured ? 'Settings' : 'Set up'}</button>}
        {open && (
          <>
            <ol className="small muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6 }}>
              <li>In Telegram, open <b>@BotFather</b>, send <code>/newbot</code>, choose a name. It replies with a <b>token</b>.</li>
              <li>Paste the token below and save.</li>
              <li>Open your new bot in Telegram and press <b>Start</b> (or send it any message).</li>
              <li>Click <b>Detect chat</b>, then <b>Send test</b>.</li>
            </ol>
            <div className="row">
              <input type="text" className="grow" placeholder={cfg?.has_token ? `saved (${cfg.token_hint})` : '123456:ABC-DEF…'} value={token} onChange={e => setToken(e.target.value)} />
              <button className="btn sm" disabled={!token} onClick={() => run(() => live.setTelegram({ token }), 'Token saved.').then(() => setToken(''))}>Save</button>
            </div>
            <div className="row">
              <button className="btn sm" disabled={!cfg?.has_token} onClick={() => run(() => live.detectTelegram(), 'Chat found.')}>Detect chat</button>
              <button className="btn sm" disabled={!cfg?.configured} onClick={() => run(() => live.testTelegram(), 'Test message sent — check your phone.')}>Send test</button>
              {cfg?.chat_id && <span className="small faint num">chat {cfg.chat_id}</span>}
            </div>
            <span className="small faint">The token is stored only on this PC.</span>
          </>
        )}
        {msg && <div className={`small ${msg.ok ? 'pos' : 'neg'}`}>{msg.text}</div>}
      </div>
    </div>
  )
}
