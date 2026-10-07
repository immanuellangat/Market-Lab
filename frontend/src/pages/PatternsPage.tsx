import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, discovery, type CompareRow, type PatternDetail, type PatternRow, type RareEvent, type Run, type TestResult } from '../api'
import { fmt, useApp } from '../state'
import { StudyPanel } from './ExecutionPanels'
import { CompareTable, Results } from './ResearchPage'

type Tab = 'library' | 'run' | 'rare' | 'study'
const STATUS_HELP: Record<string, string> = {
  validated: 'Held up on data the search never saw, after correcting for the number of candidates tested.',
  promising: 'Positive on unseen data, but not yet statistically conclusive — needs more occurrences.',
  failed: 'Looked good in-sample but did not hold up on unseen data. Kept for transparency.',
}

/** Open the chart at the latest candle with this pattern drawn on it. */
const patternSetup = (r: PatternRow, name: string) => ({
  symbol: r.symbol, tf: r.tf, time: Math.floor(Date.now() / 1000), replay: false,
  setup: { conditions: r.conditions, direction: r.direction, name, target_r: r.target_r, stop_atr: r.stop_atr, cooldown: r.cooldown },
})
const statusCls = (s: string) => (s === 'validated' ? 'bull' : s === 'promising' ? 'warn' : 'bear')

export default function PatternsPage({ theme }: { theme: string }) {
  const [tab, setTab] = useState<Tab>('library')
  const [openId, setOpenId] = useState<number | null>(null)
  if (openId !== null) return <PatternView id={openId} theme={theme} onBack={() => setOpenId(null)} />
  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Discovered patterns</h1>
          <p>The engine searches thousands of condition combinations on older data, then confirms the best on recent data it never saw.</p>
        </div>
        <div className="seg">
          <button className={tab === 'library' ? 'on' : ''} onClick={() => setTab('library')}>Pattern library</button>
          <button className={tab === 'run' ? 'on' : ''} onClick={() => setTab('run')}>Run discovery</button>
          <button className={tab === 'rare' ? 'on' : ''} onClick={() => setTab('rare')}>Rare events now</button>
          <button className={tab === 'study' ? 'on' : ''} onClick={() => setTab('study')}>Execution study</button>
        </div>
      </div>
      {tab === 'library' && <Library onOpen={setOpenId} onRun={() => setTab('run')} />}
      {tab === 'run' && <RunPanel />}
      {tab === 'rare' && <RarePanel />}
      {tab === 'study' && <StudyPanel />}
    </div>
  )
}

// ------------------------------------------------------------------ library
function Library({ onOpen, onRun }: { onOpen: (id: number) => void; onRun: () => void }) {
  const { openChartAt } = useApp()
  const { meta } = useApp()
  const [rows, setRows] = useState<PatternRow[] | null>(null)
  const [f, setF] = useState({ symbol: '', tf: '', direction: '', status: 'validated,promising', min_n: 0 })
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<keyof PatternRow>('score')
  const [err, setErr] = useState<string | null>(null)
  const byId = useMemo(() => Object.fromEntries((meta?.conditions ?? []).map(c => [c.id, c.label])), [meta])
  const lbl = useCallback((c: string) => (c.startsWith('!') ? `NOT ${byId[c.slice(1)] ?? c}` : byId[c] ?? c), [byId])

  useEffect(() => {
    discovery.patterns({ symbol: f.symbol || undefined, tf: f.tf || undefined, direction: f.direction || undefined, status: f.status, min_n: f.min_n })
      .then(setRows).catch(e => setErr(String(e.message ?? e)))
  }, [f])

  const shown = (rows ?? [])
    .filter(r => !q || r.conditions.map(lbl).join(' ').toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => {
      const rank = { validated: 0, promising: 1, failed: 2 }
      if (rank[a.status] !== rank[b.status]) return rank[a.status] - rank[b.status]  // validated always first
      return ((b[sort] as number) ?? -1e9) - ((a[sort] as number) ?? -1e9)
    })
  const statusSet = new Set(f.status.split(','))
  const toggleStatus = (s: string) => {
    const n = new Set(statusSet); if (n.has(s)) n.delete(s); else n.add(s)
    setF({ ...f, status: [...n].join(',') })
  }
  const head = (k: keyof PatternRow, label: string, cls = 'r') => (
    <th className={cls} style={{ cursor: 'pointer', color: sort === k ? 'var(--text)' : undefined }} onClick={() => setSort(k)}>{label}{sort === k ? ' ↓' : ''}</th>
  )

  return (
    <>
      <div className="toolbar">
        <select value={f.symbol} onChange={e => setF({ ...f, symbol: e.target.value })}><option value="">All instruments</option>{meta?.symbols.map(s => <option key={s.id}>{s.id}</option>)}</select>
        <select value={f.tf} onChange={e => setF({ ...f, tf: e.target.value })}><option value="">All timeframes</option>{meta?.timeframes.map(t => <option key={t}>{t}</option>)}</select>
        <select value={f.direction} onChange={e => setF({ ...f, direction: e.target.value })}><option value="">Long & short</option><option value="long">Long</option><option value="short">Short</option></select>
        {(['validated', 'promising', 'failed'] as const).map(s => (
          <button key={s} className={`btn sm ${statusSet.has(s) ? 'on' : ''}`} title={STATUS_HELP[s]} onClick={() => toggleStatus(s)}>{s}</button>
        ))}
        <span className="small muted">min occurrences</span>
        <input type="number" style={{ width: 80 }} value={f.min_n} min={0} onChange={e => setF({ ...f, min_n: Number(e.target.value) })} />
        <input type="search" className="grow" placeholder="Search conditions…" value={q} onChange={e => setQ(e.target.value)} />
      </div>
      {err && <div className="error">{err}</div>}
      {rows && rows.length === 0 && (
        <div className="notice">No patterns match these filters yet. <button className="btn sm primary" onClick={onRun}>Run discovery</button></div>
      )}
      {shown.length > 0 && (
        <div className="panel" style={{ overflowX: 'auto' }}>
          <table className="t num">
            <thead><tr>
              <th>Grade</th><th>Pattern</th><th>Market</th><th>Status</th>
              {head('n_all', 'n')}{head('win_rate', 'Win')}{head('avg_r', 'Avg R')}{head('oos_avg_r', 'Unseen avg R')}{head('oos_n', 'Unseen n')}{head('profit_factor', 'PF')}{head('stability', 'Stable yrs')}{head('score', 'Score')}<th />
            </tr></thead>
            <tbody>
              {shown.map(r => (
                <tr key={r.id} className="click" onClick={() => onOpen(r.id)}>
                  <td><span className={`grade-chip ${r.grade}`}>{r.grade}</span></td>
                  <td style={{ fontFamily: 'inherit', maxWidth: 460 }}>
                    <div className="row" style={{ gap: 4 }}>{r.conditions.map(c => <span key={c} className={`chip ${c.startsWith('!') ? 'bear' : ''}`}>{lbl(c)}</span>)}</div>
                    <div className="small faint">{r.source}</div>
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}><span className={`chip ${r.direction === 'long' ? 'bull' : 'bear'}`}>{r.direction === 'long' ? 'BUY' : 'SELL'}</span> {r.symbol} {r.tf}</td>
                  <td><span className={`chip ${statusCls(r.status)}`} title={STATUS_HELP[r.status]}>{r.status}</span></td>
                  <td className="r">{fmt.int(r.n_all)}</td>
                  <td className="r">{fmt.pct(r.win_rate, 0)}</td>
                  <td className={`r ${(r.avg_r ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmt.r(r.avg_r)}</td>
                  <td className={`r ${(r.oos_avg_r ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmt.r(r.oos_avg_r)}</td>
                  <td className="r">{fmt.int(r.oos_n)}</td>
                  <td className="r">{fmt.num(r.profit_factor)}</td>
                  <td className="r">{r.stability === null ? '—' : fmt.pct(r.stability, 0)}</td>
                  <td className="r">{r.score}</td>
                  <td className="r"><button className="btn sm" title="Show this pattern on the chart: its conditions now and every past occurrence"
                    onClick={e => { e.stopPropagation(); openChartAt(patternSetup(r, r.conditions.map(lbl).join(' + '))) }}>View on chart</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

// ------------------------------------------------------------------ run discovery
function RunPanel() {
  const { meta } = useApp()
  const [syms, setSyms] = useState<string[]>(['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD', 'BTCUSD', 'US100'])
  const [tfs, setTfs] = useState<string[]>(['H1'])
  const [dirs, setDirs] = useState<string[]>(['long', 'short'])
  const [target, setTarget] = useState(2)
  const [state, setState] = useState<Awaited<ReturnType<typeof discovery.runs>> | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const refresh = useCallback(() => discovery.runs().then(setState).catch(e => setErr(String(e.message ?? e))), [])
  useEffect(() => { refresh(); const h = setInterval(refresh, 4000); return () => clearInterval(h) }, [refresh])
  const tog = (arr: string[], set: (v: string[]) => void, v: string) => set(arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v])
  const jobs = syms.length * tfs.length * dirs.length
  const heavy = tfs.includes('M5') || tfs.includes('M15')

  const start = async () => {
    setErr(null)
    try { await discovery.start({ symbols: syms, tfs, directions: dirs, target_r: target, stop_atr: 1 }); refresh() }
    catch (e) { setErr((e as Error).message) }
  }
  return (
    <div className="grid2" style={{ alignItems: 'start' }}>
      <div className="panel">
        <div className="panel-head"><h3>New discovery run</h3></div>
        <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div className="field"><label>Instruments</label>
            <div className="row">{meta?.symbols.map(s => <button key={s.id} className={`btn sm ${syms.includes(s.id) ? 'on' : ''}`} onClick={() => tog(syms, setSyms, s.id)}>{s.id}</button>)}</div></div>
          <div className="field"><label>Timeframes</label>
            <div className="row">{meta?.timeframes.map(t => <button key={t} className={`btn sm ${tfs.includes(t) ? 'on' : ''}`} onClick={() => tog(tfs, setTfs, t)}>{t}</button>)}</div></div>
          <div className="form-grid">
            <div className="field"><label>Directions</label>
              <div className="row">{['long', 'short'].map(d => <button key={d} className={`btn sm ${dirs.includes(d) ? 'on' : ''}`} onClick={() => tog(dirs, setDirs, d)}>{d}</button>)}</div></div>
            <div className="field"><label>Target</label>
              <div className="seg">{[1, 2, 3].map(t => <button key={t} className={t === target ? 'on' : ''} onClick={() => setTarget(t)}>{t}R</button>)}</div></div>
          </div>
          <div className="small muted">{jobs} searches · stop 1×ATR · each tests ~10–20k combinations. {heavy ? 'M5/M15 searches take a few minutes each.' : 'H1 and above take under a minute each.'}</div>
          <div className="small faint">Re-running a market replaces its previous results. Patterns are searched on the oldest 70% of history and confirmed on the most recent 30%.</div>
          <button className="btn primary" disabled={!jobs} onClick={start}>Start discovery</button>
          {err && <div className="error small">{err}</div>}
        </div>
      </div>
      <div className="panel">
        <div className="panel-head"><h3>Runs</h3>{state?.worker.current && <span className="small row"><span className="spinner" /> {state.worker.current}</span>}</div>
        <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {state?.runs.length === 0 && <span className="small muted">No runs yet.</span>}
          {state?.runs.map((r: Run) => (
            <div key={r.id} className="zone-card" style={{ cursor: 'default' }}>
              <div className="row">
                <b>#{r.id}</b>
                <span className="small muted">{r.params.symbols.join(', ')} · {r.params.tfs.join(', ')} · {r.params.directions.join('/')} · {r.params.target_r}R</span>
                <span className="grow" />
                <span className={`chip ${r.status === 'done' ? 'bull' : r.status === 'running' ? 'accent' : r.status === 'failed' ? 'bear' : ''}`}>{r.status}</span>
              </div>
              {(r.status === 'running' || r.status === 'queued') && (
                <div className="row small">
                  <div className="bar-track grow"><div className="bar-fill" style={{ width: `${r.total ? (r.progress / r.total) * 100 : 0}%`, background: 'var(--accent)' }} /></div>
                  <span className="num">{r.progress}/{r.total}</span>
                  <button className="btn sm ghost" onClick={() => discovery.cancel(r.id).then(refresh)}>Cancel</button>
                </div>
              )}
              {r.status === 'running' && r.message && <div className="small faint">{r.message}</div>}
              {r.summary && <div className="small num">{fmt.int(r.summary.tested)} combinations tested → <span className="pos">{r.summary.validated} validated</span> · <span style={{ color: 'var(--warn)' }}>{r.summary.promising} promising</span> · <span className="neg">{r.summary.failed} failed</span></div>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ rare events
function RarePanel() {
  const { meta, symbol, setSymbol, tf, setTf, openChartAt } = useApp()
  const [res, setRes] = useState<Awaited<ReturnType<typeof discovery.rare>> | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const scan = async () => {
    setBusy(true); setErr(null)
    try { setRes(await discovery.rare(symbol, tf)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <>
      <div className="toolbar">
        <select value={symbol} onChange={e => setSymbol(e.target.value)}>{meta?.symbols.map(s => <option key={s.id}>{s.id}</option>)}</select>
        <div className="seg">{meta?.timeframes.map(t => <button key={t} className={t === tf ? 'on' : ''} onClick={() => setTf(t)}>{t}</button>)}</div>
        <button className="btn primary" disabled={busy} onClick={scan}>{busy && <span className="spinner" />} Scan latest candle</button>
        <span className="small faint">Looks for conditions, combinations and extremes that occurred on fewer than 1% of past candles.</span>
      </div>
      {err && <div className="error">{err}</div>}
      {res && (
        <div className="small muted">Latest closed candle {fmt.local(res.time)} · {res.active_conditions} conditions active · outcomes measured {res.horizon} candles later.
          <b> Rare situations have small samples by definition — context, not an edge.</b></div>
      )}
      {res && res.events.length === 0 && <div className="notice">Nothing unusual on the latest {res.symbol} {res.tf} candle.</div>}
      <div className="grid2">
        {res?.events.map((e: RareEvent, k) => (
          <div key={k} className="panel">
            <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div className="row">
                <span className="chip warn">{e.kind}</span>
                <span className="small muted num">about 1 in {fmt.int(1 / Math.max(e.freq, 1e-6))} candles</span>
              </div>
              <div className="row">{e.labels.map(l => <span key={l} className="chip accent">{l}</span>)}</div>
              <div className="kpis">
                <div className="kpi"><div className="k">Past occurrences</div><div className="v">{e.n}</div></div>
                <div className="kpi"><div className="k">Higher {res.horizon} bars later</div><div className="v">{fmt.pct(e.up_pct, 0)}</div><div className="s">avg move {fmt.num(e.fwd_mean_atr)} ATR</div></div>
                <div className="kpi"><div className="k">Avg max up / down</div><div className="v" style={{ fontSize: 15 }}>{fmt.num(e.max_up_atr, 1)} / {fmt.num(e.max_down_atr, 1)}</div><div className="s">ATR</div></div>
                <div className="kpi"><div className="k">Long / short at 2R</div><div className="v" style={{ fontSize: 15 }}><span className={(e.long_avg_r ?? 0) >= 0 ? 'pos' : 'neg'}>{fmt.r(e.long_avg_r)}</span> / <span className={(e.short_avg_r ?? 0) >= 0 ? 'pos' : 'neg'}>{fmt.r(e.short_avg_r)}</span></div></div>
              </div>
              <table className="t num">
                <tbody>
                  {e.examples.slice(0, 5).map(x => (
                    <tr key={x.time}>
                      <td>{fmt.local(x.time)}</td>
                      <td className={`r ${x.fwd_atr >= 0 ? 'pos' : 'neg'}`}>{x.fwd_atr >= 0 ? '+' : ''}{fmt.num(x.fwd_atr)} ATR</td>
                      <td className="r"><button className="btn sm" onClick={() => openChartAt({ symbol: res.symbol, tf: res.tf, time: x.time, replay: true, label: 'Rare event' })}>Replay</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

// ------------------------------------------------------------------ pattern detail
function PatternView({ id, theme, onBack }: { id: number; theme: string; onBack: () => void }) {
  const { openChartAt } = useApp()
  const [p, setP] = useState<PatternDetail | null>(null)
  const [full, setFull] = useState<TestResult | null>(null)
  const [extra, setExtra] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [exFilter, setExFilter] = useState<'all' | 'win' | 'loss' | 'oos'>('all')

  const load = useCallback(() => discovery.pattern(id).then(setP).catch(e => setErr(String(e.message ?? e))), [id])
  useEffect(() => { load() }, [load])
  // poll while a comparison is running
  useEffect(() => {
    if (!p?.compare || p.compare.status === 'done') return
    const h = setInterval(load, 3000)
    return () => clearInterval(h)
  }, [p?.compare, load])
  // full analysis (examples, breakdowns, best/worst conditions, decay)
  useEffect(() => {
    if (!p) return
    setBusy(true)
    api.test({ symbol: p.symbol, tf: p.tf, conditions: [...p.conditions, ...extra], direction: p.direction, target_r: p.target_r,
      stop_atr: p.stop_atr, cooldown: p.cooldown, include_costs: true })
      .then(setFull).catch(e => setErr(String(e.message ?? e))).finally(() => setBusy(false))
  }, [p?.id, extra]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!p) return <div className="page">{err ? <div className="error">{err}</div> : <span className="spinner" />}</div>
  const d = p.data
  return (
    <div className="page">
      <div className="row"><button className="btn sm" onClick={onBack}>← All patterns</button>
        <button className="btn sm primary" onClick={() => openChartAt(patternSetup(p, d.labels.join(' + ')))}>View on chart</button></div>
      <div className="panel">
        <div className="panel-body row" style={{ alignItems: 'flex-start', gap: 14 }}>
          <div className={`grade ${p.grade}`}>{p.grade}</div>
          <div className="grow" style={{ minWidth: 260, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="row">
              <span className={`chip ${p.direction === 'long' ? 'bull' : 'bear'}`}>{p.direction === 'long' ? 'BUY' : 'SELL'}</span>
              <b>{p.symbol} {p.tf}</b>
              <span className={`chip ${statusCls(p.status)}`}>{p.status}</span>
              <span className="chip">found by {p.source}</span>
            </div>
            <div className="row">{d.labels.map(l => <span key={l} className={`chip ${l.startsWith('NOT ') ? 'bear' : 'accent'}`}>{l}</span>)}</div>
            <p style={{ margin: 0, lineHeight: 1.55 }}>{d.description}</p>
            <div className="small muted">{STATUS_HELP[p.status]} Out-of-sample q-value {fmt.num(p.q_oos, 3)} (≤ 0.10 needed) · the search tested {fmt.int(d.extra.n_tested)} hypotheses for this market · unseen data starts {fmt.day(d.extra.split_time)}
              {p.stability !== null && <> · beat random entry in {fmt.pct(p.stability, 0)} of in-sample years</>}.</div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h3>Search period vs unseen data</h3></div>
        <div className="panel-body" style={{ overflowX: 'auto' }}><CompareTable is={d.is} oos={d.oos} all={d.all} /></div>
      </div>

      {d.extra.cluster_profile && (
        <div className="panel">
          <div className="panel-head"><h3>Situation profile (cluster)</h3><span className="small faint">{fmt.pct(d.extra.cluster_share, 1)} of in-sample candles</span></div>
          <div className="panel-body">
            <table className="t num"><thead><tr><th>Measure</th><th className="r">In this situation</th><th className="r">Typical</th></tr></thead>
              <tbody>{Object.entries(d.extra.cluster_profile).map(([k, v]) => (
                <tr key={k}><td style={{ fontFamily: 'inherit' }}>{k}</td><td className="r">{fmt.num(v.cluster)}</td><td className="r muted">{fmt.num(v.overall)}</td></tr>
              ))}</tbody></table>
          </div>
        </div>
      )}

      <ComparePanel p={p} onStart={() => discovery.compare(p.id).then(load)} onOpen={(s, t) => openChartAt({ symbol: s, tf: t, time: Math.floor(Date.now() / 1000), replay: false })} />

      <div className="row">
        <h3 style={{ fontSize: 14 }}>Full analysis</h3>
        {extra.length > 0 && <><span className="small muted">with extra filter:</span>{extra.map(x => <span key={x} className="chip accent">{x}<button onClick={() => setExtra(extra.filter(y => y !== x))}>✕</button></span>)}</>}
        {busy && <span className="spinner" />}
      </div>
      {full && <Results key={JSON.stringify([full.conditions, full.params])} res={full} theme={theme} onAdd={id2 => setExtra([...extra, id2])}
        exFilter={exFilter} setExFilter={setExFilter}
        onOpen={(t, replay) => openChartAt({ symbol: p.symbol, tf: p.tf, time: t, replay, label: 'Pattern', direction: p.direction })} />}
    </div>
  )
}

function ComparePanel({ p, onStart, onOpen }: { p: PatternDetail; onStart: () => void; onOpen: (s: string, tf: string) => void }) {
  const c = p.compare
  const table = (rows: CompareRow[], key: 'symbol' | 'tf') => (
    <table className="t num">
      <thead><tr><th>{key === 'symbol' ? 'Instrument' : 'Timeframe'}</th><th className="r">n</th><th className="r">Win</th><th className="r">Avg R</th><th className="r">vs random</th><th className="r">Unseen avg R</th><th className="r">Grade</th></tr></thead>
      <tbody>{rows.map(r => {
        const self = r.symbol === p.symbol && r.tf === p.tf
        return (
          <tr key={r[key]} style={{ fontWeight: self ? 600 : undefined }} className="click" onClick={() => onOpen(r.symbol, r.tf)}>
            <td>{r[key]}{self ? ' (found here)' : ''}</td>
            {r.error ? <td colSpan={6} className="muted small">{r.error}</td> : <>
              <td className="r">{fmt.int(r.n)}</td><td className="r">{fmt.pct(r.win_rate, 0)}</td>
              <td className={`r ${(r.avg_r ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmt.r(r.avg_r)}</td>
              <td className={`r ${(r.edge_r ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmt.r(r.edge_r)}</td>
              <td className={`r ${(r.oos_avg_r ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmt.r(r.oos_avg_r)}</td>
              <td className="r">{r.grade}</td></>}
          </tr>
        )
      })}</tbody>
    </table>
  )
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Does it work elsewhere?</h3>
        {(!c || c.status === 'done') && <button className="btn sm" onClick={onStart}>{c ? 'Recompute' : 'Compare across instruments & timeframes'}</button>}
        {c && c.status !== 'done' && <span className="small row"><span className="spinner" /> {c.status}…</span>}
      </div>
      {c && (c.symbols?.length > 0 || c.timeframes?.length > 0) && (
        <div className="panel-body grid2">
          <div>{table(c.symbols ?? [], 'symbol')}</div>
          <div>{table(c.timeframes ?? [], 'tf')}</div>
        </div>
      )}
      {!c && <div className="panel-body small muted">Tests these exact conditions on every instrument (same timeframe) and every timeframe (same instrument). A robust behaviour usually shows up in more than one place.</div>}
    </div>
  )
}
