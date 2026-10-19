import { useEffect, useMemo, useState } from 'react'
import { execution, type PortfolioResult, type PortfolioSettings } from '../api'
import MiniLine from '../chart/MiniLine'
import { fmt, useApp } from '../state'

const pct = (x: number | null | undefined, d = 1) => fmt.pct(x, d)

export default function PortfolioPage({ theme }: { theme: string }) {
  const { setPage } = useApp()
  const [cfg, setCfg] = useState<PortfolioSettings | null>(null)
  const [res, setRes] = useState<PortfolioResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    execution.portfolio().then(r => { setCfg(r.settings); setRes(r.result) }).catch(e => setErr((e as Error).message))
  }, [])

  const run = async () => {
    if (!cfg) return
    setBusy(true); setErr(null)
    try { setRes(await execution.runPortfolio(cfg)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const set = <K extends keyof PortfolioSettings>(k: K, v: PortfolioSettings[K]) => setCfg(c => (c ? { ...c, [k]: v } : c))

  const names = useMemo(() => Object.fromEntries((res?.setups ?? []).map(s => [s.id, s])), [res])
  const eqLine = useMemo(() => res ? [{ points: res.account.curve, color: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#4f8cff' }] : [], [res, theme])
  const dupes = (res?.overlap ?? []).filter(o => o.overlap_share >= 0.9 && (o.weekly_corr ?? 0) >= 0.9)
  const linked = (res?.overlap ?? []).filter(o => !dupes.includes(o) && ((o.weekly_corr ?? 0) >= 0.3 || o.overlap_share >= 0.3))
  const a = res?.account
  const mc = res?.monte_carlo
  const maxYear = Math.max(0.01, ...(a?.yearly ?? []).map(y => Math.abs(y.return)))

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Portfolio</h1>
          <p>All active watched setups traded together as one account, over their historical trades — with account rules, a stress test and how much the setups overlap.</p>
        </div>
      </div>

      {cfg && (
        <div className="panel">
          <div className="panel-body row" style={{ gap: 14, alignItems: 'flex-end' }}>
            <Field label="Starting balance"><input type="number" min="1" value={cfg.balance} onChange={e => set('balance', +e.target.value)} style={{ width: 110 }} /></Field>
            <Field label="Risk per trade %"><input type="number" min="0.05" step="0.05" value={cfg.risk_pct} onChange={e => set('risk_pct', +e.target.value)} style={{ width: 80 }} /></Field>
            <Field label="Max open trades"><input type="number" min="1" value={cfg.max_open} onChange={e => set('max_open', +e.target.value)} style={{ width: 70 }} /></Field>
            <Field label="Max total open risk %"><input type="number" min="0.1" step="0.5" value={cfg.max_open_risk} onChange={e => set('max_open_risk', +e.target.value)} style={{ width: 80 }} /></Field>
            <Field label="Drawdown you can stomach %"><input type="number" min="1" value={cfg.max_dd_tolerance} onChange={e => set('max_dd_tolerance', +e.target.value)} style={{ width: 80 }} /></Field>
            <Field label="Period">
              <div className="seg">
                <button className={cfg.period === 'all' ? 'on' : ''} onClick={() => set('period', 'all')}>All history</button>
                <button className={cfg.period === 'unseen' ? 'on' : ''} onClick={() => set('period', 'unseen')} title="Only the most recent 30% the setups were never tuned on">Unseen only</button>
              </div>
            </Field>
            <label className="row small" style={{ gap: 6, cursor: 'pointer' }}>
              <input type="checkbox" checked={cfg.one_per_symbol} onChange={e => set('one_per_symbol', e.target.checked)} /> One trade per instrument
            </label>
            <span className="grow" />
            <button className="btn primary" disabled={busy} onClick={run}>{busy && <span className="spinner" />} {busy ? 'Simulating… (~1 min)' : 'Run simulation'}</button>
          </div>
        </div>
      )}

      {err && <div className="error">{err}</div>}
      {!res && !busy && cfg && <div className="notice">No simulation yet. Set your account rules and click <b>Run simulation</b>.</div>}

      {res && a && (
        <>
          <div className="small muted">
            Simulated {fmt.day(a.start)} → {fmt.day(a.end)} ({fmt.num(a.years, 1)} years) · {res.config.period === 'unseen' ? 'unseen data only' : 'all history (includes the period the setups were found on — optimistic)'} · risk {res.config.risk_pct}% per trade · computed {fmt.date(res.computed)} UTC
          </div>

          {dupes.length > 0 && (
            <div className="error" style={{ background: 'transparent' }}>
              ⚠ Duplicate watches: {dupes.map(d => `#${d.a} and #${d.b}`).join(', ')} take the same trades ({pct(dupes[0].overlap_share, 0)} overlap, correlation {fmt.num(dupes[0].weekly_corr, 2)}).
              Live they alert twice for one trade; consider removing the copies on the <a href="#" onClick={e => { e.preventDefault(); setPage('scanner') }}>Live signals</a> page.
            </div>
          )}

          <div className="stat-grid">
            <Stat label="Final balance" value={fmt.num(a.final_equity, 0)} sub={`${a.total_return >= 0 ? '+' : ''}${pct(a.total_return, 0)} total`} pos={a.total_return >= 0} />
            <Stat label="Yearly growth (CAGR)" value={pct(a.cagr)} pos={a.cagr >= 0} />
            <Stat label="Worst drawdown" value={pct(a.max_dd)} sub={`longest ${fmt.int(a.longest_dd_days)} days below a peak`} neg />
            <Stat label="Trades" value={fmt.int(a.trades)} sub={`${fmt.num(a.trades_per_month, 1)} per month`} />
            <Stat label="Win rate · avg" value={pct(a.win_rate, 0)} sub={`${fmt.r(a.avg_r)} per trade`} />
            <Stat label="Worst losing streak" value={String(a.worst_losing_streak)} sub={`${pct(a.profitable_months, 0)} of months profitable`} />
          </div>

          <div className="panel">
            <div className="panel-head"><h3>Account balance</h3><span className="small faint">equity after each closed trade</span></div>
            <div className="panel-body"><MiniLine lines={eqLine} theme={theme} zeroLine={false} /></div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))', gap: 12 }}>
            <div className="panel">
              <div className="panel-head"><h3>Return per year</h3></div>
              <div className="panel-body">
                <table className="t num">
                  <tbody>
                    {a.yearly.map(y => (
                      <tr key={y.year} title={`${y.year}: ${y.return >= 0 ? '+' : ''}${(y.return * 100).toFixed(1)}%`}>
                        <td style={{ width: 50 }}>{y.year}</td>
                        <td style={{ width: '60%' }}>
                          <div style={{ display: 'flex', alignItems: 'center', height: 14 }}>
                            <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-end' }}>
                              {y.return < 0 && <div style={{ width: `${(-y.return / maxYear) * 100}%`, height: 10, background: 'var(--bear)', borderRadius: '4px 0 0 4px' }} />}
                            </div>
                            <div style={{ width: 1, height: 14, background: 'var(--faint)' }} />
                            <div style={{ flex: 1 }}>
                              {y.return >= 0 && <div style={{ width: `${(y.return / maxYear) * 100}%`, height: 10, background: 'var(--bull)', borderRadius: '0 4px 4px 0' }} />}
                            </div>
                          </div>
                        </td>
                        <td className={`r ${y.return >= 0 ? 'pos' : 'neg'}`}>{y.return >= 0 ? '+' : ''}{pct(y.return)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="panel">
              <div className="panel-head"><h3>Contribution per setup</h3></div>
              <div className="panel-body">
                <table className="t num">
                  <thead><tr><th>Setup</th><th className="r">Trades</th><th className="r">Win</th><th className="r">Avg R</th><th className="r">Profit</th></tr></thead>
                  <tbody>
                    {[...a.per_setup].sort((x, y) => y.pnl - x.pnl).map(p => {
                      const s = names[p.setup]
                      return (
                        <tr key={p.setup}>
                          <td style={{ fontFamily: 'inherit' }} title={s?.name}>#{p.setup} {s ? `${s.symbol} ${s.tf} ${s.direction === 'long' ? 'BUY' : s.direction === 'short' ? 'SELL' : 'BUY/SELL'}` : ''}</td>
                          <td className="r">{p.trades}</td>
                          <td className="r">{pct(p.wins / Math.max(p.trades, 1), 0)}</td>
                          <td className={`r ${(p.avg_r ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmt.r(p.avg_r)}</td>
                          <td className={`r ${p.pnl >= 0 ? 'pos' : 'neg'}`}>{fmt.num(p.pnl, 0)}</td>
                        </tr>
                      )
                    })}
                    {res.setups.filter(s => !a.per_setup.some(p => p.setup === s.id)).map(s => (
                      <tr key={s.id} className="muted"><td style={{ fontFamily: 'inherit' }}>#{s.id} {s.symbol} {s.tf}</td><td className="r" colSpan={4}>{s.error ? `error: ${s.error}` : 'no trades taken (skipped by the account rules)'}</td></tr>
                    ))}
                  </tbody>
                </table>
                {Object.keys(a.skipped).length > 0 && (
                  <div className="small muted" style={{ marginTop: 8 }}>
                    Skipped by account rules: {Object.entries(a.skipped).map(([k, v]) => `${fmt.int(v)} × ${k}`).join(' · ')}
                  </div>
                )}
              </div>
            </div>
          </div>

          {mc && 'table' in mc && mc.table && (
            <div className="panel">
              <div className="panel-head">
                <h3>How much to risk per trade — stress test</h3>
                <span className="small faint">{fmt.int(mc.runs)} reshuffled histories of {fmt.int(mc.trades_per_run)} trades</span>
              </div>
              <div className="panel-body">
                <table className="t num">
                  <thead><tr><th>Risk per trade</th><th className="r">Typical return</th><th className="r">Bad-luck return (5%)</th><th className="r">Typical drawdown</th><th className="r">Bad-luck drawdown (95%)</th><th className="r">Chance drawdown &gt; {res.config.max_dd_tolerance}%</th></tr></thead>
                  <tbody>
                    {mc.table.map(t => {
                      const best = t.risk_pct === mc.suggested_risk_pct
                      return (
                        <tr key={t.risk_pct} style={{ fontWeight: best ? 600 : undefined }}>
                          <td>{t.risk_pct}%{best && <span className="chip bull" style={{ marginLeft: 6 }}>suggested</span>}</td>
                          <td className="r">{pct(t.median_return, 0)}</td>
                          <td className="r">{pct(t.p5_return, 0)}</td>
                          <td className="r">{pct(t.median_dd, 1)}</td>
                          <td className={`r ${t.p95_dd > res.config.max_dd_tolerance / 100 ? 'neg' : ''}`}>{pct(t.p95_dd, 1)}</td>
                          <td className={`r ${t.p_dd_over_limit > 0.05 ? 'neg' : ''}`}>{pct(t.p_dd_over_limit, 1)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                <div className="small muted" style={{ marginTop: 8 }}>
                  {mc.suggested_risk_pct
                    ? <>The highest risk whose bad-luck (95th percentile) drawdown stays within your {res.config.max_dd_tolerance}% limit is <b>{mc.suggested_risk_pct}% per trade</b>.</>
                    : <>No risk level keeps the bad-luck drawdown within {res.config.max_dd_tolerance}% — lower the risk or accept a larger drawdown.</>}
                  {' '}Returns compound, so they grow very fast with risk on paper — the drawdown columns are the ones to respect.
                </div>
              </div>
            </div>
          )}

          {(linked.length > 0 || dupes.length > 0) && (
            <div className="panel">
              <div className="panel-head"><h3>Setups that move together</h3></div>
              <div className="panel-body">
                <table className="t num">
                  <thead><tr><th>Pair</th><th className="r">In the market together</th><th className="r">Weekly result correlation</th><th /></tr></thead>
                  <tbody>
                    {[...dupes, ...linked].map(o => (
                      <tr key={`${o.a}-${o.b}`}>
                        <td style={{ fontFamily: 'inherit' }}>#{o.a} {names[o.a]?.symbol} {names[o.a]?.tf} · #{o.b} {names[o.b]?.symbol} {names[o.b]?.tf}</td>
                        <td className="r">{pct(o.overlap_share, 0)}</td>
                        <td className="r">{fmt.num(o.weekly_corr, 2)}</td>
                        <td className="r">{dupes.includes(o) ? <span className="chip bear">duplicate</span> : <span className="chip warn">linked</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="small muted" style={{ marginTop: 8 }}>Linked setups are effectively one bigger bet when they fire together; the account rules (one trade per instrument, max open risk) limit that.</div>
              </div>
            </div>
          )}

          <div className="small faint">Historical simulation, not a forecast. Each setup is traded with its own exit plan; costs (spread) are included. Live results will differ — compare with each watch's live record.</div>
        </>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}><span className="small muted">{label}</span>{children}</label>
}

function Stat({ label, value, sub, pos, neg }: { label: string; value: string; sub?: string; pos?: boolean; neg?: boolean }) {
  return (
    <div className="panel">
      <div className="panel-body">
        <div className="small muted">{label}</div>
        <div className={`num ${pos ? 'pos' : neg ? 'neg' : ''}`} style={{ fontSize: 22, fontWeight: 600 }}>{value}</div>
        {sub && <div className="small faint">{sub}</div>}
      </div>
    </div>
  )
}
