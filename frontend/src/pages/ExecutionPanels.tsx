import { useCallback, useEffect, useState } from 'react'
import { execution, live, type OptimizeResult, type PlanResult, type StudyResult, type TestResult } from '../api'
import { fmt, useApp } from '../state'

const VERDICT: Record<string, [string, string]> = {
  improved: ['bull', 'The in-sample choice also beat the baseline on unseen data.'],
  similar: ['', 'On unseen data the chosen plan performed about the same as the baseline.'],
  worse: ['bear', 'The in-sample favourite did WORSE than the baseline on unseen data — classic over-optimisation.'],
  insufficient: ['', 'Too few unseen signals to judge.'],
}

/** Per-setup entry/exit optimisation (shown under research results and on pattern pages). */
export function ExecutionPanel({ res }: { res: TestResult }) {
  const { setPage } = useApp()
  const [out, setOut] = useState<OptimizeResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [watched, setWatched] = useState<string | null>(null)

  const run = async () => {
    setBusy(true); setErr(null)
    try { setOut(await execution.optimize({ symbol: res.symbol, tf: res.tf, conditions: res.conditions, direction: res.direction, cooldown: res.params.cooldown })) }
    catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const watch = async (key: string) => {
    setErr(null)
    try {
      await live.addWatch({ symbol: res.symbol, tf: res.tf, conditions: res.conditions, direction: res.direction as 'long' | 'short',
        target_r: res.params.target_r, stop_atr: res.params.stop_atr, cooldown: res.params.cooldown, plan: key })
      setWatched(key)
    } catch (e) { setErr((e as Error).message) }
  }

  const row = (r: PlanResult, tag?: string) => {
    const base = out!.baseline.oos.per_signal_r ?? 0
    const d = (r.oos.per_signal_r ?? 0) - base
    return (
      <tr key={r.key + (tag ?? '')} style={{ fontWeight: tag ? 600 : undefined }}>
        <td style={{ fontFamily: 'inherit', maxWidth: 380 }}>{tag && <span className="chip accent" style={{ marginRight: 6 }}>{tag}</span>}{r.description}</td>
        <td className="r">{fmt.r(r.is.per_signal_r)}</td><td className="r">{fmt.pct(r.is.win_rate, 0)}</td>
        <td className={`r ${(r.oos.per_signal_r ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmt.r(r.oos.per_signal_r)}</td>
        <td className="r">{fmt.pct(r.oos.win_rate, 0)}</td>
        <td className="r">{r.is.taken + r.oos.taken}/{r.is.signals + r.oos.signals}</td>
        <td className={`r ${d >= 0 ? 'pos' : 'neg'}`}>{r.key === out!.baseline.key ? '—' : fmt.r(d)}</td>
      </tr>
    )
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Optimise entries & exits</h3>
        <button className="btn sm" disabled={busy} onClick={run}>{busy && <span className="spinner" />} {out ? 'Re-run' : 'Test ~70 entry/exit plans'}</button>
      </div>
      <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {!out && !busy && <span className="small muted">Tests stop types (ATR, structure, lower-timeframe swing), targets (1.5R–3R, nearest liquidity), break-even, partial profits, trailing stops and lower-timeframe confirmation entries. The plan is chosen on the search period only; its unseen-data result is shown honestly.</span>}
        {busy && <span className="small muted">Simulating every occurrence under every plan…</span>}
        {err && <div className="error small">{err}</div>}
        {out && (
          <>
            <div className="row">
              <span className={`chip ${VERDICT[out.verdict][0]}`}>{out.verdict}</span>
              <span className="small">{VERDICT[out.verdict][1]}</span>
            </div>
            <div className="small muted">R per signal = average result counting skipped signals (no lower-timeframe confirmation) as 0R. Unseen data starts {fmt.day(out.split_time)}.</div>
            <div style={{ overflowX: 'auto' }}>
              <table className="t num">
                <thead><tr><th>Plan</th><th className="r">Search R/signal</th><th className="r">Win</th><th className="r">Unseen R/signal</th><th className="r">Win</th><th className="r">Trades</th><th className="r">vs baseline (unseen)</th></tr></thead>
                <tbody>
                  {row(out.baseline, 'baseline')}
                  {row(out.best, 'chosen in-sample')}
                  {out.ranked.filter(r => r.key !== out.best.key && r.key !== out.baseline.key).slice(0, 8).map(r => row(r))}
                </tbody>
              </table>
            </div>
            <div className="zone-card" style={{ cursor: 'default' }}>
              <div className="row"><b>Recommendation</b><span className="grow" />
                {watched === out.recommendation.key
                  ? <button className="btn sm on" onClick={() => setPage('scanner')}>✓ Watching — view live signals</button>
                  : <button className="btn sm primary" onClick={() => watch(out.recommendation.key)}>Watch live with this plan</button>}
              </div>
              <div className="small">{out.recommendation.description}</div>
              <div className="small muted">{out.recommendation.reason}</div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

/** Pooled study: which execution rules help across many setups on unseen data. */
export function StudyPanel() {
  const [st, setSt] = useState<Awaited<ReturnType<typeof execution.study>> | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const refresh = useCallback(() => execution.study().then(setSt).catch(e => setErr(String(e.message ?? e))), [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    if (!st?.state.running) return
    const h = setInterval(refresh, 4000)
    return () => clearInterval(h)
  }, [st?.state.running, refresh])
  const r: StudyResult | null | undefined = st?.result
  const s = st?.state
  const compName: Record<string, string> = { entry: 'Entry', stop: 'Stop', target: 'Target', manage: 'Management' }

  return (
    <>
      <div className="panel">
        <div className="panel-body row" style={{ gap: 12 }}>
          <div className="grow small muted" style={{ minWidth: 280 }}>
            Applies ~70 entry/exit plans to 240 standard setups (FVG and order-block retests, sweeps, BOS/CHoCH, divergence, candle patterns on every instrument, H1 and H4)
            and compares each with the baseline (next-candle entry, 1×ATR stop, 2R target) on unseen data. Standard setups are used because discovered patterns were
            selected under the baseline exit, which would bias the comparison. A plan only counts as robust if it helps consistently across setups.
          </div>
          {s?.running
            ? <span className="row small"><span className="spinner" /> {s.progress}/{s.total} · {s.message}</span>
            : <button className="btn primary" onClick={() => execution.startStudy().then(refresh).catch(e => setErr(String(e.message ?? e)))}>{r ? 'Re-run study' : 'Run execution study'}</button>}
        </div>
      </div>
      {err && <div className="error">{err}</div>}
      {r && (
        <>
          <div className="small muted">{r.setups} setups ({(r as StudyResult & { source?: string }).source === 'patterns' ? 'discovered patterns — biased toward the baseline' : 'standard concepts — unbiased'}) · finished {fmt.local(r.finished)} · {r.rule}</div>
          <div className="kpis">
            <div className="kpi"><div className="k">Robust improvements</div><div className={`v ${r.robust.length ? 'pos' : ''}`}>{r.robust.length}</div><div className="s">of {r.plans.length - 1} alternative plans</div></div>
            {Object.entries(r.components).map(([part, vals]) => {
              const best = Object.entries(vals).sort((a, b) => b[1] - a[1])[0]
              return <div key={part} className="kpi"><div className="k">Best {compName[part]} (avg)</div><div className="v" style={{ fontSize: 14 }}>{best[0]}</div><div className="s">{fmt.r(best[1])} vs baseline</div></div>
            })}
          </div>
          <div className="panel" style={{ overflowX: 'auto' }}>
            <table className="t num">
              <thead><tr><th>Plan</th><th className="r">Setups</th><th className="r">Avg Δ unseen</th><th className="r">Median Δ</th><th className="r">Improved in</th><th className="r">Δ in search period</th><th className="r">Δ win rate</th><th /></tr></thead>
              <tbody>{r.plans.map(p => (
                <tr key={p.key} style={{ opacity: p.robust ? 1 : 0.8 }}>
                  <td style={{ fontFamily: 'inherit', maxWidth: 420 }}>{p.description}</td>
                  <td className="r">{p.setups}</td>
                  <td className={`r ${p.mean_d_oos >= 0 ? 'pos' : 'neg'}`}>{fmt.r(p.mean_d_oos, 3)}</td>
                  <td className={`r ${p.median_d_oos >= 0 ? 'pos' : 'neg'}`}>{fmt.r(p.median_d_oos, 3)}</td>
                  <td className="r">{fmt.pct(p.share_improved, 0)}</td>
                  <td className={`r ${p.mean_d_is >= 0 ? 'pos' : 'neg'}`}>{fmt.r(p.mean_d_is, 3)}</td>
                  <td className={`r ${p.mean_d_winrate >= 0 ? 'pos' : 'neg'}`}>{p.mean_d_winrate >= 0 ? '+' : ''}{fmt.num(p.mean_d_winrate * 100, 1)} pts</td>
                  <td>{p.robust && <span className="chip bull">robust</span>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}
