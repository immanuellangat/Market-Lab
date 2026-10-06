import { useMemo, useState } from 'react'
import { api, live, type BreakdownRow, type ConditionMeta, type Summary, type TestResult } from '../api'
import MiniLine from '../chart/MiniLine'
import { ExecutionPanel } from './ExecutionPanels'
import { fmt, useApp } from '../state'

const BREAKDOWNS: [string, string][] = [
  ['session', 'By session'], ['dow', 'By day of week'], ['volatility', 'By volatility regime'],
  ['structure', 'By market structure'], ['year', 'By year'], ['hour_ny', 'By hour (New York time)'],
]

function cssVar(n: string) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim() }

export default function ResearchPage({ theme }: { theme: string }) {
  const { meta, symbol, setSymbol, tf, setTf, openChartAt } = useApp()
  const [conds, setConds] = useState<string[]>(['sweep_low_recent', 'fvg_bull_retest'])
  const [direction, setDirection] = useState<'long' | 'short'>('long')
  const [target, setTarget] = useState(2)
  const [stopAtr, setStopAtr] = useState(1)
  const [cooldown, setCooldown] = useState(5)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [costs, setCosts] = useState(true)
  const [q, setQ] = useState('')
  const [res, setRes] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [exFilter, setExFilter] = useState<'all' | 'win' | 'loss' | 'oos'>('all')

  const byId = useMemo(() => Object.fromEntries((meta?.conditions ?? []).map(c => [c.id, c])), [meta])
  const grouped = useMemo(() => {
    const g: Record<string, ConditionMeta[]> = {}
    for (const c of meta?.conditions ?? []) {
      if (q && !(`${c.label} ${c.description} ${c.category}`.toLowerCase().includes(q.toLowerCase()))) continue
      ;(g[c.category] ??= []).push(c)
    }
    return g
  }, [meta, q])

  const toggle = (id: string) => setConds(c => (c.includes(id) ? c.filter(x => x !== id) : [...c, id]))

  const run = async (override?: string[]) => {
    const use = override ?? conds
    if (!use.length) { setErr('Pick at least one condition.'); return }
    setBusy(true); setErr(null)
    try {
      setRes(await api.test({ symbol, tf, conditions: use, direction, target_r: target, stop_atr: stopAtr, cooldown,
        date_from: dateFrom || null, date_to: dateTo || null, include_costs: costs }))
    } catch (e) { setErr((e as Error).message); setRes(null) } finally { setBusy(false) }
  }

  // suggest direction from the bias of the chosen conditions
  const bias = conds.reduce((a, id) => a + (byId[id]?.bias === 'bull' ? 1 : byId[id]?.bias === 'bear' ? -1 : 0), 0)

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Research lab</h1>
          <p>Define a hypothesis as a combination of conditions and measure how it really performed, in-sample and out-of-sample.</p>
        </div>
      </div>
      <div className="lab">
        <div className="panel">
          <div className="panel-head"><h3>Hypothesis</h3></div>
          <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div className="form-grid">
              <div className="field"><label>Instrument</label>
                <select value={symbol} onChange={e => setSymbol(e.target.value)}>{meta?.symbols.map(s => <option key={s.id}>{s.id}</option>)}</select></div>
              <div className="field"><label>Timeframe</label>
                <select value={tf} onChange={e => setTf(e.target.value)}>{meta?.timeframes.map(t => <option key={t}>{t}</option>)}</select></div>
            </div>
            <div className="field"><label>Direction</label>
              <div className="row">
                <div className="seg">
                  <button className={direction === 'long' ? 'on' : ''} onClick={() => setDirection('long')}>Long</button>
                  <button className={direction === 'short' ? 'on' : ''} onClick={() => setDirection('short')}>Short</button>
                </div>
                {bias !== 0 && (bias > 0) !== (direction === 'long') && <span className="chip warn">conditions lean {bias > 0 ? 'bullish' : 'bearish'}</span>}
              </div>
            </div>
            <div className="form-grid">
              <div className="field"><label>Target</label>
                <div className="seg">{(meta?.targets ?? [1, 2, 3]).map(t => <button key={t} className={t === target ? 'on' : ''} onClick={() => setTarget(t)}>{t}R</button>)}</div></div>
              <div className="field"><label>Stop (× ATR)</label>
                <input type="number" min={0.25} max={5} step={0.25} value={stopAtr} onChange={e => setStopAtr(Number(e.target.value))} /></div>
              <div className="field"><label>Min. bars between occurrences</label>
                <input type="number" min={0} max={500} value={cooldown} onChange={e => setCooldown(Number(e.target.value))} /></div>
              <div className="field"><label>Include spread cost</label>
                <label className="row small"><input type="checkbox" checked={costs} onChange={e => setCosts(e.target.checked)} /> deduct spread</label></div>
              <div className="field"><label>From</label><input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} /></div>
              <div className="field"><label>To</label><input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} /></div>
            </div>

            <div className="field">
              <label>Conditions (all must be true at the signal bar)</label>
              <div className="row">
                {conds.length === 0 && <span className="small faint">None selected</span>}
                {conds.map(id => (
                  <span key={id} className={`chip ${byId[id]?.bias === 'bull' ? 'bull' : byId[id]?.bias === 'bear' ? 'bear' : ''}`}>
                    {byId[id]?.label ?? id}<button onClick={() => toggle(id)} aria-label="remove">✕</button>
                  </span>
                ))}
              </div>
            </div>
            <input type="search" placeholder="Search conditions (e.g. sweep, FVG, London, H4)…" value={q} onChange={e => setQ(e.target.value)} />
            <div className="cond-list">
              {Object.entries(grouped).map(([cat, list]) => (
                <div key={cat}>
                  <div className="cond-cat">{cat}</div>
                  {list.map(c => (
                    <div key={c.id} className={`cond ${conds.includes(c.id) ? 'on' : ''}`} title={c.description} onClick={() => toggle(c.id)}>
                      <span className={`dot ${c.bias}`} />{c.label}
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <button className="btn primary" disabled={busy} onClick={() => run()}>{busy ? <span className="spinner" /> : null} Test hypothesis</button>
            {err && <div className="error small">{err}</div>}
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          {!res && !busy && (
            <div className="notice">
              Results appear here: occurrences, win rate with confidence bounds, expectancy in R versus random entry,
              out-of-sample confirmation, an evidence grade, breakdowns by session / day / volatility, and every historical
              example — each one openable on the chart in replay mode.
            </div>
          )}
          {res && <Results key={JSON.stringify([res.symbol, res.tf, res.direction, res.conditions, res.params])} res={res} theme={theme} onAdd={id => { const next = [...conds, id]; setConds(next); run(next) }}
            exFilter={exFilter} setExFilter={setExFilter}
            onOpen={(t, replay) => openChartAt({ symbol: res.symbol, tf: res.tf, time: t, replay, label: 'Setup', direction: res.direction })} />}
        </div>
      </div>
    </div>
  )
}

export function Results({ res, theme, onAdd, onOpen, exFilter, setExFilter }: {
  res: TestResult; theme: string; onAdd: (id: string) => void; onOpen: (t: number, replay: boolean) => void
  exFilter: string; setExFilter: (f: 'all' | 'win' | 'loss' | 'oos') => void
}) {
  const { all, in_sample: is, out_of_sample: oos } = res.stats
  const eqLines = useMemo(() => {
    const eq = res.equity ?? []
    const isPts = eq.filter(p => !p.oos)
    const oosPts = eq.filter(p => p.oos)
    if (isPts.length && oosPts.length) oosPts.unshift(isPts[isPts.length - 1])
    return [
      { points: isPts, color: cssVar('--accent'), title: 'in-sample' },
      { points: oosPts, color: cssVar('--warn'), title: 'out-of-sample' },
    ]
  }, [res, theme]) // eslint-disable-line react-hooks/exhaustive-deps
  const rollLines = useMemo(() => [{ points: res.rolling ?? [], color: cssVar('--bull'), title: 'rolling avg R' }], [res, theme]) // eslint-disable-line react-hooks/exhaustive-deps
  const examples = (res.examples ?? []).filter(e => exFilter === 'all' || (exFilter === 'oos' ? e.oos : e.outcome === exFilter))
  const { setPage } = useApp()
  const [watch, setWatch] = useState<{ state: 'idle' | 'busy' | 'done' | 'error'; msg?: string }>({ state: 'idle' })
  const weak = ['D', 'F'].includes(res.evidence.grade)
  const addWatch = async () => {
    if (weak && !confirm(`This setup has grade ${res.evidence.grade}: the historical evidence for an edge is weak. Watch it anyway?`)) return
    setWatch({ state: 'busy' })
    try {
      await live.addWatch({ symbol: res.symbol, tf: res.tf, conditions: res.conditions, direction: res.direction as 'long' | 'short',
        target_r: res.params.target_r, stop_atr: res.params.stop_atr, cooldown: res.params.cooldown })
      setWatch({ state: 'done' })
    } catch (e) { setWatch({ state: 'error', msg: (e as Error).message }) }
  }

  return (
    <>
      <div className="panel">
        <div className="panel-body row" style={{ alignItems: 'flex-start', gap: 14 }}>
          <div className={`grade ${res.evidence.grade}`}>{res.evidence.grade}</div>
          <div className="grow" style={{ minWidth: 240 }}>
            <div className="row" style={{ marginBottom: 6 }}>
              <b>{res.symbol} {res.tf} · {res.direction.toUpperCase()}</b>
              <span className="chip">evidence score {res.evidence.score}/100</span>
              <span className="small faint">target {res.params.target_r}R · stop {res.params.stop_atr}×ATR · horizon {res.params.horizon} bars</span>
              <span className="grow" />
              {watch.state === 'done'
                ? <button className="btn sm on" onClick={() => setPage('scanner')}>✓ Watching — view live signals</button>
                : <button className="btn sm primary" disabled={watch.state === 'busy'} onClick={addWatch}>{watch.state === 'busy' && <span className="spinner" />} Watch this setup live</button>}
            </div>
            {watch.state === 'error' && <div className="error small" style={{ marginBottom: 6 }}>{watch.msg}</div>}
            <div className="row" style={{ marginBottom: 6 }}>{res.condition_labels.map(l => <span key={l} className="chip accent">{l}</span>)}</div>
            <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
              {res.evidence.reasons.map(r => <li key={r}>{r}</li>)}
            </ul>
            <div className="small faint" style={{ marginTop: 6 }}>
              The grade measures how strong and repeatable the historical evidence is — it is not a probability that the next trade wins.
              Out-of-sample = data after {fmt.day(res.split_time)}, never used to judge the in-sample result.
            </div>
          </div>
        </div>
      </div>

      {all.n === 0 ? <div className="notice">This combination never occurred in the selected data. Remove a condition or widen the date range.</div> : (
        <>
          <div className="kpis">
            <Kpi k="Occurrences" v={fmt.int(all.n)} s={all.trades_per_year ? `${fmt.num(all.trades_per_year, 1)} per year` : ''} />
            <Kpi k={`Win rate (${res.params.target_r}R)`} v={fmt.pct(all.win_rate)} s={`≥ ${fmt.pct(all.win_rate_lb)} at 95% conf. · breakeven ${fmt.pct(all.breakeven_win_rate, 0)}`} />
            <Kpi k="Expectancy" v={fmt.r(all.avg_r, 3)} cls={(all.avg_r ?? 0) >= 0 ? 'pos' : 'neg'} s={all.avg_r_ci ? `90% CI ${fmt.r(all.avg_r_ci[0])} … ${fmt.r(all.avg_r_ci[1])}` : ''} />
            <Kpi k="Edge vs random entry" v={fmt.r(all.edge_r, 3)} cls={(all.edge_r ?? 0) >= 0 ? 'pos' : 'neg'} s={`random: ${fmt.r(all.baseline_r, 3)}`} />
            <Kpi k="Profit factor" v={fmt.num(all.profit_factor)} s={`total ${fmt.r(all.total_r, 1)}`} />
            <Kpi k="Max drawdown" v={fmt.r(-(all.max_dd_r ?? 0), 1)} cls="neg" s={`worst streak ${all.max_losing_streak} losses`} />
          </div>

          <div className="panel">
            <div className="panel-head"><h3>In-sample vs out-of-sample</h3><span className="small faint">a real edge should survive on unseen data</span></div>
            <div className="panel-body" style={{ overflowX: 'auto' }}>
              <CompareTable is={is} oos={oos} all={all} />
            </div>
          </div>

          <div className="grid2">
            <div className="panel">
              <div className="panel-head"><h3>Equity curve (R)</h3><span className="row small"><span className="chip accent">in-sample</span><span className="chip warn">out-of-sample</span></span></div>
              <div className="panel-body"><MiniLine lines={eqLines} theme={theme} /></div>
            </div>
            <div className="panel">
              <div className="panel-head"><h3>Stability over time</h3>
                {res.decay && <span className={`chip ${res.decay.status === 'stable' || res.decay.status === 'improving' ? 'bull' : res.decay.status === 'insufficient' ? '' : 'bear'}`}>{res.decay.status}</span>}
              </div>
              <div className="panel-body">
                <MiniLine lines={rollLines} theme={theme} />
                {res.decay && <div className="small muted" style={{ marginTop: 6 }}>{res.decay.message}
                  {res.decay.recent_avg_r !== undefined && <> Recent {res.decay.recent_n} trades: <span className="num">{fmt.r(res.decay.recent_avg_r)}</span> vs earlier <span className="num">{fmt.r(res.decay.earlier_avg_r)}</span>.</>}
                </div>}
              </div>
            </div>
          </div>

          <ExecutionPanel res={res} />

          {res.best_worst && (res.best_worst.best.length > 0 || res.best_worst.worst.length > 0) && (
            <div className="grid2">
              <FilterPanel title="Performs best when…" rows={res.best_worst.best} onAdd={onAdd} good />
              <FilterPanel title="Performs worst when…" rows={res.best_worst.worst} onAdd={onAdd} />
            </div>
          )}

          <div className="grid2">
            {BREAKDOWNS.map(([k, title]) => res.breakdowns?.[k] && <Breakdown key={k} title={title} rows={res.breakdowns[k]} />)}
          </div>

          <div className="panel">
            <div className="panel-head">
              <h3>Historical examples</h3>
              <div className="seg">
                {(['all', 'win', 'loss', 'oos'] as const).map(f => <button key={f} className={exFilter === f ? 'on' : ''} onClick={() => setExFilter(f)}>{f === 'oos' ? 'out-of-sample' : f}</button>)}
              </div>
            </div>
            <div className="panel-body" style={{ maxHeight: 420, overflow: 'auto' }}>
              <table className="t num">
                <thead><tr><th>Signal bar (UTC)</th><th>Outcome</th><th className="r">R</th><th className="r">Max favourable (ATR)</th><th className="r">Bars</th><th>Session</th><th>Set</th><th /></tr></thead>
                <tbody>
                  {examples.slice(0, 400).map(e => (
                    <tr key={e.i}>
                      <td>{fmt.date(e.time)}</td>
                      <td><span className={`chip ${e.outcome === 'win' ? 'bull' : e.outcome === 'loss' ? 'bear' : ''}`}>{e.outcome}</span></td>
                      <td className={`r ${e.r >= 0 ? 'pos' : 'neg'}`}>{fmt.r(e.r)}</td>
                      <td className="r">{fmt.num(e.mfe_atr)}</td>
                      <td className="r">{fmt.int(e.bars)}</td>
                      <td>{e.session.replace('_', ' ')}</td>
                      <td className="muted">{e.oos ? 'OOS' : 'IS'}</td>
                      <td className="r" style={{ whiteSpace: 'nowrap' }}>
                        <button className="btn sm" onClick={() => onOpen(e.time, false)}>View</button>{' '}
                        <button className="btn sm" onClick={() => onOpen(e.time, true)}>Replay</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {examples.length > 400 && <div className="small faint" style={{ marginTop: 6 }}>Showing the 400 most recent of {examples.length}.</div>}
            </div>
          </div>
        </>
      )}
    </>
  )
}

function Kpi({ k, v, s, cls }: { k: string; v: string; s?: string; cls?: string }) {
  return <div className="kpi"><div className="k">{k}</div><div className={`v ${cls ?? ''}`}>{v}</div>{s && <div className="s">{s}</div>}</div>
}

export function CompareTable({ is, oos, all }: { is: Summary; oos: Summary; all: Summary }) {
  const rows: [string, (s: Summary) => string][] = [
    ['Occurrences', s => fmt.int(s.n)],
    ['Wins / losses / timeouts', s => (s.n ? `${s.wins} / ${s.losses} / ${s.timeouts}` : '—')],
    ['Win rate', s => fmt.pct(s.win_rate)],
    ['Win rate, 95% lower bound', s => fmt.pct(s.win_rate_lb)],
    ['Expectancy per trade', s => fmt.r(s.avg_r, 3)],
    ['Random-entry expectancy', s => fmt.r(s.baseline_r, 3)],
    ['Edge vs random', s => fmt.r(s.edge_r, 3)],
    ['Shrunk expectancy (small-sample adjusted)', s => fmt.r(s.shrunk_r, 3)],
    ['p-value (edge > 0)', s => fmt.num(s.p_value, 4)],
    ['Profit factor', s => fmt.num(s.profit_factor)],
    ['Max drawdown', s => (s.n ? fmt.r(-(s.max_dd_r ?? 0), 1) : '—')],
    ['Avg max favourable / adverse (ATR)', s => (s.n ? `${fmt.num(s.avg_mfe_atr)} / ${fmt.num(s.avg_mae_atr)}` : '—')],
    ['Avg bars in trade', s => fmt.num(s.avg_bars, 1)],
  ]
  return (
    <table className="t num cmp">
      <thead><tr><th>Metric</th><th className="r">In-sample</th><th className="r">Out-of-sample</th><th className="r">All data</th></tr></thead>
      <tbody>{rows.map(([label, f]) => <tr key={label}><td className="muted" style={{ fontFamily: 'inherit' }}>{label}</td><td className="r">{f(is)}</td><td className="r">{f(oos)}</td><td className="r">{f(all)}</td></tr>)}</tbody>
    </table>
  )
}

function Breakdown({ title, rows }: { title: string; rows: BreakdownRow[] }) {
  const max = Math.max(0.01, ...rows.map(r => Math.abs(r.avg_r)))
  return (
    <div className="panel">
      <div className="panel-head"><h3>{title}</h3></div>
      <div className="panel-body" style={{ maxHeight: 300, overflow: 'auto' }}>
        <table className="t num">
          <thead><tr><th /><th className="r">n</th><th className="r">Win</th><th className="r">Avg R</th><th style={{ width: '35%' }} /></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.key} style={{ opacity: r.n < 20 ? 0.55 : 1 }} title={r.n < 20 ? 'Small sample — treat with caution' : ''}>
                <td>{r.key.replace('_', ' ')}</td><td className="r">{r.n}</td><td className="r">{fmt.pct(r.win_rate, 0)}</td>
                <td className={`r ${r.avg_r >= 0 ? 'pos' : 'neg'}`}>{fmt.r(r.avg_r)}</td>
                <td><div className="bar-track"><div className="bar-fill" style={{ width: `${(Math.abs(r.avg_r) / max) * 100}%`, background: r.avg_r >= 0 ? 'var(--bull)' : 'var(--bear)' }} /></div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function FilterPanel({ title, rows, onAdd, good }: { title: string; rows: { id: string; label: string; n: number; avg_r: number; win_rate: number; delta: number }[]; onAdd: (id: string) => void; good?: boolean }) {
  return (
    <div className="panel">
      <div className="panel-head"><h3>{title}</h3><span className="small faint">extra condition at the signal bar</span></div>
      <div className="panel-body">
        <table className="t num">
          <thead><tr><th /><th className="r">n</th><th className="r">Avg R</th><th className="r">Δ</th><th /></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id}>
                <td style={{ fontFamily: 'inherit' }}>{r.label}</td><td className="r">{r.n}</td>
                <td className={`r ${r.avg_r >= 0 ? 'pos' : 'neg'}`}>{fmt.r(r.avg_r)}</td>
                <td className={`r ${r.delta >= 0 ? 'pos' : 'neg'}`}>{fmt.r(r.delta)}</td>
                <td className="r">{good && <button className="btn sm" onClick={() => onAdd(r.id)}>+ add</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="small faint" style={{ marginTop: 6 }}>Δ uses shrunk averages so small subsets can't look better than they are. Adding filters raises overfitting risk — check the out-of-sample column.</div>
      </div>
    </div>
  )
}
