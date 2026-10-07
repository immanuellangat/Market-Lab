import { useEffect, useState } from 'react'
import { live, type PositionSize } from './api'
import { fmt } from './state'

function stored(key: string, fallback: string) {
  try { return localStorage.getItem(key) ?? fallback } catch { return fallback }
}
function store(key: string, v: string) {
  try { localStorage.setItem(key, v) } catch { /* storage unavailable */ }
}

/** Lot size for a chosen risk, and the money outcome at stop and target (spread included). */
export default function RiskCalc({ symbol, entry, stop, target }: { symbol: string; entry: number; stop: number; target: number | null }) {
  const [mode, setMode] = useState<'money' | 'pct'>(() => (stored('risk_mode', 'money') as 'money' | 'pct'))
  const [amount, setAmount] = useState(() => stored('risk_amount', '10'))
  const [res, setRes] = useState<PositionSize | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    const v = parseFloat(amount)
    if (!isFinite(v) || v <= 0) { setRes(null); return }
    store('risk_mode', mode); store('risk_amount', amount)
    let stopped = false
    const h = setTimeout(() => {
      live.positionSize({ symbol, entry, stop, target, ...(mode === 'money' ? { risk: v } : { risk_pct: v }) })
        .then(r => { if (!stopped) { setRes(r); setErr(null) } })
        .catch(e => { if (!stopped) { setRes(null); setErr((e as Error).message) } })
    }, 250)
    return () => { stopped = true; clearTimeout(h) }
  }, [symbol, entry, stop, target, mode, amount])

  const cur = res?.account?.currency ?? 'USD'
  const money = (x: number | undefined) => (x === undefined ? '—' : `${x.toFixed(2)} ${cur}`)
  return (
    <div className="zone-card" style={{ cursor: 'default' }}>
      <b style={{ fontSize: 13 }}>Risk calculator</b>
      <div className="row" style={{ gap: 6 }}>
        <span className="small muted">Risk</span>
        <input type="number" min="0" step="any" value={amount} onChange={e => setAmount(e.target.value)} style={{ width: 90 }} aria-label="Risk amount" />
        <div className="seg">
          <button className={mode === 'money' ? 'on' : ''} onClick={() => setMode('money')}>{cur}</button>
          <button className={mode === 'pct' ? 'on' : ''} onClick={() => setMode('pct')} title="Percent of your MT5 account balance">% balance</button>
        </div>
      </div>
      {err && <div className="small neg">{err}</div>}
      {res && (
        <>
          <div className="lv num">
            <span className="muted">Lot size</span><b>{fmt.num(res.lots, 2)}</b><span className="faint">lots</span>
            <span className="muted">Loss at stop</span><span className="neg">−{money(res.loss_at_stop)}</span>
            <span className="faint">{res.risk_pct_of_balance !== undefined ? `${res.risk_pct_of_balance}% of balance` : ''}</span>
            {res.profit_at_target !== undefined && <>
              <span className="muted">Profit at target</span><span className="pos">+{money(res.profit_at_target)}</span>
              <span className="faint">{res.rr !== undefined ? `${fmt.num(res.rr, 2)} R:R` : ''}</span>
            </>}
            <span className="muted">Spread cost</span><span>{money(res.spread_cost)}</span><span className="faint">included above</span>
          </div>
          {res.note && <div className="small neg">⚠ {res.note}</div>}
          {res.account && <div className="small faint">MT5 balance {money(res.account.balance)} · leverage 1:{res.account.leverage}</div>}
          <div className="small faint">Lots are rounded down to the broker's step so the loss never exceeds your risk (except when even the minimum lot is larger).</div>
        </>
      )}
    </div>
  )
}
