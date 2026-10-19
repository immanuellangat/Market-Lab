import { useEffect, useState } from 'react'
import { live, type Scoreboard, type ScoreRow } from '../api'
import { fmt } from '../state'

const VERDICT: Record<string, { cls: string; tip: string }> = {
  'on track': { cls: 'bull', tip: 'Enough forward trades and nothing worse than the unseen backtest.' },
  underperforming: { cls: 'bear', tip: 'Win rate or R per trade is worse than the unseen backtest at the 5% level.' },
  'too early': { cls: '', tip: 'Fewer than 10 forward trades: results are still mostly luck.' },
  'no expectation': { cls: 'warn', tip: 'Too few unseen backtest trades to know what to expect.' },
}

export default function ForwardPage() {
  const [sb, setSb] = useState<Scoreboard | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<number | null>(null)

  const load = async (force = false) => {
    setBusy(true); setErr(null)
    try { setSb(await live.scoreboard(force)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  useEffect(() => { load() }, [])

  const rows = (sb?.watches ?? []).slice().sort((a, b) => Number(b.active) - Number(a.active) || a.id - b.id)
  const t = sb?.total

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Forward tests</h1>
          <p>Each watched setup's trades since its rule was chosen — data it was never fitted on — compared with what its unseen backtest promised.</p>
        </div>
        <button className="btn" disabled={busy} onClick={() => load(true)}>{busy && <span className="spinner" />} {busy ? 'Rebuilding… (~1 min)' : 'Refresh'}</button>
      </div>

      {err && <div className="error">{err}</div>}
      {!sb && busy && <div className="notice">Rebuilding every watch's trades…</div>}

      {sb && t && (
        <>
          <div className="kpis">
            <div className="kpi"><div className="k">Forward trades (active watches)</div><div className="v">{fmt.int(t.n)}</div><div className="s">closed since each rule was chosen</div></div>
            <div className="kpi"><div className="k">Won</div><div className="v">{t.win_rate === null ? '—' : fmt.pct(t.win_rate, 0)}</div><div className="s">target reached</div></div>
            <div className="kpi"><div className="k">R per trade</div><div className={`v ${(t.avg_r ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmt.r(t.avg_r)}</div><div className="s">total {fmt.r(t.total_r)}</div></div>
            <div className="kpi"><div className="k">Verdicts</div><div className="v">{rows.filter(r => r.verdict === 'underperforming').length} ⚠</div><div className="s">{rows.filter(r => r.verdict === 'on track').length} on track · {rows.filter(r => r.verdict === 'too early').length} too early</div></div>
          </div>
          <div className="small muted">{sb.rule} Updated {fmt.date(sb.as_of)} UTC.</div>

          <div className="panel">
            <div className="panel-body" style={{ overflowX: 'auto' }}>
              <table className="t num">
                <thead>
                  <tr>
                    <th>Watch</th><th>Forward since</th><th className="r">Signals</th><th className="r">Won</th><th className="r">R / trade</th>
                    <th className="r">Expected</th><th>Verdict</th><th className="r">Alerts sent</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => <Row key={r.id} r={r} open={open === r.id} onToggle={() => setOpen(open === r.id ? null : r.id)} />)}
                </tbody>
              </table>
            </div>
          </div>
          {sb.errors.length > 0 && <div className="error">Could not score: {sb.errors.map(e => `#${e.id} (${e.error})`).join(', ')}</div>}
        </>
      )}
    </div>
  )
}

function Row({ r, open, onToggle }: { r: ScoreRow; open: boolean; onToggle: () => void }) {
  const e = r.expected, f = r.forward
  const v = VERDICT[r.verdict] ?? { cls: '', tip: '' }
  const signals = f.n + r.open
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: 'pointer', opacity: r.active ? 1 : 0.55 }}>
        <td style={{ maxWidth: 360 }}>
          <b>#{r.id}</b> {r.symbol} {r.tf} {r.direction}{!r.active && ' · paused'}
          <div className="small muted" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={r.name}>{r.name}</div>
        </td>
        <td>{fmt.day(r.since)}<div className="small faint">{fmt.int(r.days)} days</div></td>
        <td className="r">{signals}{f.expected_signals !== undefined && <div className="small faint">~{fmt.num(f.expected_signals, 1)} expected</div>}</td>
        <td className="r">{f.n ? `${f.wins}/${f.n}` : '—'}{f.n ? <div className="small faint">{fmt.pct(f.win_rate, 0)}</div> : null}</td>
        <td className={`r ${(f.avg_r ?? 0) >= 0 ? 'pos' : 'neg'}`}>{f.n ? fmt.r(f.avg_r) : '—'}</td>
        <td className="r">{e.n ? <>{fmt.pct(e.win_rate, 0)} · {fmt.r(e.avg_r)}<div className="small faint">{fmt.num(e.per_month, 1)}/month · {e.n} unseen trades</div></> : '—'}</td>
        <td><span className={`chip ${v.cls}`} title={v.tip}>{r.verdict}</span></td>
        <td className="r" title="Alerts the app actually sent for these trades (it only alerts while it is running)">
          {r.alerts.matched}/{signals}{r.alerts.missed > 0 && <div className="small" style={{ color: 'var(--warn)' }}>{r.alerts.missed} missed</div>}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={8} style={{ background: 'var(--panel-2)' }}>
            {r.checks.map((c, k) => (
              <div key={k} className="small" style={{ color: c.ok ? 'var(--muted)' : 'var(--bear)' }}>
                {c.ok ? '✓' : '✗'} <b>{c.check}</b>: {c.text}{c.p !== null && ` (p=${c.p.toFixed(3)})`}
              </div>
            ))}
            {r.trades.length > 0 ? (
              <table className="t num" style={{ marginTop: 8 }}>
                <thead><tr><th>Entry</th><th>Exit</th><th>Side</th><th>How</th><th className="r">R</th></tr></thead>
                <tbody>
                  {r.trades.slice().reverse().map((x, k) => (
                    <tr key={k}>
                      <td>{fmt.date(x.entry_time)}</td><td>{x.closed ? fmt.date(x.exit_time) : 'open'}</td><td>{x.direction ?? r.direction}</td>
                      <td>{x.exit ?? ''}</td><td className={`r ${x.r >= 0 ? 'pos' : 'neg'}`}>{x.closed ? fmt.r(x.r) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="small faint" style={{ marginTop: 6 }}>No forward trades yet.</div>}
          </td>
        </tr>
      )}
    </>
  )
}
