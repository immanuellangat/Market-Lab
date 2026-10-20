import { useCallback, useEffect, useState } from 'react'
import { live, type ExitOption, type ExitProfiles, type LiveStatus, type Signal, type TelegramCfg, type Watch } from '../api'
import { notifyPermission, requestNotify } from '../alerts'
import RiskCalc from '../RiskCalc'
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
  const [calc, setCalc] = useState(false)
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
          {p.unproven && <span className="chip warn" title="No statistically confirmed edge yet: this signal is part of a forward test.">unproven</span>}
          {!!p.late_bars && <span className="chip warn" title="Found on catch-up: it fired while the app was closed or offline. Price has moved since — check it before acting.">late · {p.late_bars} candle{p.late_bars === 1 ? '' : 's'} ago</span>}
          <span className="grow" />
          <span className={`chip ${statusCls}`}>{s.status === 'open' ? 'open' : `${s.status} ${fmt.r(s.result_r)}`}</span>
          <span className="small faint" title={fmt.date(s.bar_time) + ' UTC'}>{ago(s.detected_at)}</span>
        </div>

        <div className="levels num">
          <div><span className="muted">Entry ≈</span> {fmt.price(s.entry)}<div className="faint small">next candle open</div></div>
          <div><span className="muted">Stop</span> <span className="neg">{fmt.price(s.stop)}</span><div className="faint small">{p.stop_text ?? `${p.stop_atr}× ATR`}</div></div>
          <div><span className="muted">Target</span> <span className="pos">{fmt.price(s.target)}</span><div className="faint small">{p.target_text ?? `${p.target_r}R`}</div></div>
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
          {s.status === 'open' && <button className="btn sm" onClick={() => setCalc(v => !v)}>{calc ? 'Hide calculator' : 'Lot size calculator'}</button>}
          <span className="grow" />
          <span className="small faint">Historical evidence, not a guarantee.</span>
        </div>

        {calc && <RiskCalc symbol={s.symbol} entry={s.entry} stop={s.stop} target={s.target} />}

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

/** The exit a watch trades with, and how that exit did in the past and on unseen data. */
function ExitLine({ w }: { w: Watch }) {
  const st = w.stats?.stats
  const i = st?.in_sample, o = st?.out_of_sample
  return (
    <div className="small" style={{ lineHeight: 1.5 }}>
      <span className="muted">Exit:</span> <b>stop {w.stop_atr}×ATR · target {w.target_r}R</b>
      {i?.n ? (
        <div className="num">
          <span className="muted">Past</span> wins {fmt.pct(i.win_rate, 0)} · {fmt.r(i.avg_r)}
          {o?.n ? <> <span className="muted">· Unseen</span> wins {fmt.pct(o.win_rate, 0)} · {fmt.r(o.avg_r)}</> : null}
          <span className="faint"> per trade</span>
        </div>
      ) : null}
    </div>
  )
}

const exitName = (x: ExitOption) => `stop ${x.stop_atr}×ATR · target ${x.target_r}R`

/** Pick a watch's exit: the two suggested profiles first, every live exit on request. */
function ExitChooser({ w, act, busy }: { w: Watch; busy: string | null; act: (l: string, f: () => Promise<unknown>) => void }) {
  const [p, setP] = useState<ExitProfiles | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [all, setAll] = useState(false)
  useEffect(() => { live.exits(w.id).then(setP).catch(e => setErr((e as Error).message)) }, [w.id])
  const inUse = (x: ExitOption) => x.stop_atr === w.stop_atr && x.target_r === w.target_r && (!w.plan || w.plan === x.plan)
  const use = (x: ExitOption) => act(`x${w.id}`, () => live.setExit(w.id, x.stop_atr, x.target_r))
  const UseBtn = ({ x }: { x: ExitOption }) => inUse(x)
    ? <span className="chip accent">in use</span>
    : <button className="btn sm primary" disabled={!!busy} onClick={() => use(x)}>{busy === `x${w.id}` && <span className="spinner" />} Use</button>
  const Profile = ({ title, hint, x }: { title: string; hint: string; x: ExitOption | null }) => (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '7px 9px' }}>
      <div className="row"><b style={{ fontSize: 12.5 }}>{title}</b><span className="grow" />{x && <UseBtn x={x} />}</div>
      {x ? (
        <div className="small num" style={{ lineHeight: 1.5 }}>
          <div>{exitName(x)}</div>
          <div>wins <b>{fmt.pct(x.is.win_rate, 0)}</b> <span className="muted">(unseen {fmt.pct(x.oos.win_rate, 0)})</span> · <b className={(x.is.avg_r ?? 0) >= 0 ? 'pos' : 'neg'}>{fmt.r(x.is.avg_r)}</b> <span className="muted">(unseen {fmt.r(x.oos.avg_r)})</span> per trade</div>
        </div>
      ) : <div className="small muted">{hint}</div>}
    </div>
  )
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
      {err && <div className="small error">{err}</div>}
      {!p && !err && <div className="small muted row"><span className="spinner" /> Testing 12 exits on this setup's history…</div>}
      {p && (
        <>
          <Profile title="Higher win rate" x={p.high_wr} hint="No exit wins often enough here while staying profitable." />
          <Profile title="Most profit per trade" x={p.best_r} hint="Not enough past trades to choose." />
          <button className="btn sm ghost" style={{ alignSelf: 'flex-start' }} onClick={() => setAll(!all)}>{all ? 'Hide' : 'Show'} all {p.exits.length} exits</button>
          {all && (
            <div style={{ overflowX: 'auto' }}>
              <table className="t num" style={{ fontSize: 11 }}>
                <thead><tr><th>Exit</th><th className="r" title="past / unseen">Wins</th><th className="r" title="past / unseen">R per trade</th><th /></tr></thead>
                <tbody>
                  {p.exits.map(x => (
                    <tr key={x.plan}>
                      <td style={{ whiteSpace: 'nowrap' }}>{x.stop_atr}×ATR·{x.target_r}R</td>
                      <td className="r" style={{ whiteSpace: 'nowrap' }}>{fmt.pct(x.is.win_rate, 0)}<span className="muted">/{fmt.pct(x.oos.win_rate, 0)}</span></td>
                      <td className="r" style={{ whiteSpace: 'nowrap' }}>
                        <span className={(x.is.avg_r ?? 0) >= 0 ? 'pos' : 'neg'}>{fmt.num(x.is.avg_r, 2)}</span><span className="muted">/{fmt.num(x.oos.avg_r, 2)}</span>
                      </td>
                      <td className="r">{inUse(x) ? <span className="faint">✓</span> : <button className="btn sm ghost" style={{ padding: '1px 4px' }} disabled={!!busy} onClick={() => use(x)}>Use</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="small faint">Each cell: past / unseen.</div>
            </div>
          )}
          <div className="small faint" style={{ lineHeight: 1.45 }}>{p.rule} Open trades keep the stop and target they were sent with.</div>
        </>
      )}
    </div>
  )
}

function WatchlistPanel({ watches, busy, act }: { watches: Watch[]; busy: string | null; act: (l: string, f: () => Promise<unknown>) => void }) {
  const { openChartAt } = useApp()
  const [choosing, setChoosing] = useState<number | null>(null)
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Watched setups</h3>
        <span className="small faint" title="Ordered by the edge each setup shows NOW: its R per trade over the most recent third of its trades. Weakening, broken or underperforming setups drop to the bottom by themselves.">
          {watches.filter(w => w.active).length} active · strongest edge first
        </span>
      </div>
      <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {watches.length === 0 && <span className="small muted">Add setups from the Research lab.</span>}
        {watches.map(w => {
          const ev = w.stats?.evidence
          const rec = w.record
          return (
            <div key={w.id} className="zone-card" style={{ cursor: 'default', opacity: w.active ? 1 : 0.55 }}>
              <div className="row">
                <span className={`chip ${w.direction === 'long' ? 'bull' : w.direction === 'short' ? 'bear' : 'accent'}`}>{w.direction === 'long' ? 'BUY' : w.direction === 'short' ? 'SELL' : 'BUY/SELL'}</span>
                <b style={{ fontSize: 13 }}>{w.symbol} {w.tf}</b>
                <span className="grow" />
                {w.conditions[0]?.startsWith('strategy:') && <span className="chip warn" title="Forward test: no statistically confirmed edge yet">unproven</span>}
                {ev && <span className="chip">Grade {ev.grade}</span>}
              </div>
              <div className="small muted">{w.name}</div>
              {w.edge && <EdgeLine e={w.edge} />}
              {!w.conditions[0]?.startsWith('strategy:') && <ExitLine w={w} />}
              {rec && <div className="small num">Live: {rec.signals} signals · {rec.wins}W / {rec.losses}L · {rec.open} open{rec.avg_r !== null ? ` · ${fmt.r(rec.avg_r)} avg` : ''}</div>}
              {choosing === w.id && <ExitChooser w={w} act={act} busy={busy} />}
              <div className="row">
                {w.conditions[0]?.startsWith('strategy:') ? (
                  <button className="btn sm" title="Show where this strategy stands now, its possible entry zone and its past trades"
                    onClick={() => openChartAt({ symbol: w.symbol, tf: w.tf, time: Math.floor(Date.now() / 1000), replay: false,
                      setup: { conditions: w.conditions, direction: w.direction, name: w.name, watch_id: w.id } })}>View on chart</button>
                ) : (
                  <button className="btn sm" title="Show this setup on the chart: which conditions hold now and every past occurrence"
                    onClick={() => openChartAt({ symbol: w.symbol, tf: w.tf, time: Math.floor(Date.now() / 1000), replay: false,
                      setup: { conditions: w.conditions, direction: w.direction as 'long' | 'short', name: w.name, target_r: w.target_r, stop_atr: w.stop_atr, cooldown: w.cooldown } })}>View on chart</button>
                )}
                {!w.conditions[0]?.startsWith('strategy:') && (
                  <button className={`btn sm ${choosing === w.id ? 'on' : ''}`} title="Choose how this setup's trades are closed: higher win rate or more profit per trade"
                    onClick={() => setChoosing(choosing === w.id ? null : w.id)}>Change exit</button>
                )}
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

function EdgeLine({ e }: { e: NonNullable<Watch['edge']> }) {
  const color = e.tier >= 3 ? 'var(--muted)' : e.tier === 2 ? 'var(--warn)' : e.tier === 1 ? 'var(--bear)' : 'var(--faint)'
  return (
    <div className="small num" style={{ color }}
      title={`Rank ${e.rank}. Edge now = R per trade over the most recent ${e.n} trades${e.was !== null ? ', compared with all trades before them' : ''}. ` +
        'The list is ordered by it (shrunk toward 0 when there are few trades), with weakening, broken and underperforming setups below the rest.'}>
      #{e.rank} · Edge now {fmt.r(e.now)}{e.was !== null ? ` (was ${fmt.r(e.was)})` : ''} · {e.tier < 3 ? '⚠ ' : ''}{e.why}
    </div>
  )
}
