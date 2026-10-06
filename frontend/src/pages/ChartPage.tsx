import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, live, type Bars, type Bias, type KeyZones, type Overlays, type TradeZone } from '../api'
import PriceChart, { type Layers } from '../chart/PriceChart'
import { TZ_LABEL, fmt, useApp } from '../state'

const LAYER_LABELS: [keyof Layers, string][] = [
  ['zones', 'Trade zones'], ['fvg', 'FVG'], ['ob', 'Order blocks'], ['structure', 'BOS / CHoCH'],
  ['liquidity', 'Liquidity'], ['swings', 'Swings'], ['levels', 'PDH/PDL · Asia'],
]
const WINDOW = 4000      // bars loaded for normal browsing
const REPLAY_AHEAD = 1500 // future bars pre-loaded (hidden) for replay

export default function ChartPage({ theme }: { theme: string }) {
  const { meta, symbol, setSymbol, tf, setTf, focus, clearFocus } = useApp()
  const [bars, setBars] = useState<Bars | null>(null)
  const [overlays, setOverlays] = useState<Overlays | null>(null)
  const [zones, setZones] = useState<KeyZones | null>(null)
  const [selZone, setSelZone] = useState<TradeZone | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [layers, setLayers] = useState<Layers>({ zones: true, fvg: true, ob: true, structure: true, liquidity: true, swings: false, levels: true })
  // replay: `cursor` is the last visible global bar index
  const [replay, setReplay] = useState(false)
  const [cursor, setCursor] = useState<number | null>(null)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(4)
  const [marker, setMarker] = useState<{ index: number; text: string; dir?: string } | null>(null)
  const [inspect, setInspect] = useState<{ index: number; time: number; conditions: { id: string; label: string; category: string; bias: Bias }[] } | null>(null)
  const [pickStart, setPickStart] = useState(false)

  const lastIndex = bars ? bars.first_index + bars.time.length - 1 : 0
  const visibleEnd = replay && cursor !== null ? cursor : lastIndex
  const showCount = bars ? visibleEnd - bars.first_index + 1 : 0

  const load = useCallback(async (endIndex?: number) => {
    setLoading(true); setError(null)
    try {
      const b = await api.bars(symbol, tf, WINDOW + (endIndex !== undefined ? REPLAY_AHEAD : 0), endIndex)
      setBars(b)
      return b
    } catch (e) {
      setError((e as Error).message); setBars(null); setOverlays(null); setZones(null)
      return null
    } finally { setLoading(false) }
  }, [symbol, tf])

  // normal load on symbol / timeframe change (unless a focus request is pending)
  useEffect(() => {
    if (focus) return
    setReplay(false); setCursor(null); setMarker(null); setSelZone(null); setInspect(null)
    load()
  }, [symbol, tf]) // eslint-disable-line react-hooks/exhaustive-deps

  // jump to a historical moment requested by another page
  useEffect(() => {
    if (!focus || focus.symbol !== symbol || focus.tf !== tf) return
    (async () => {
      const { index } = await api.indexOf(focus.symbol, focus.tf, focus.time)
      const b = await load(index + REPLAY_AHEAD)
      if (!b) return
      setMarker({ index, text: focus.label ?? 'Setup', dir: focus.direction })
      setSelZone(null)
      if (focus.replay) { setReplay(true); setCursor(index); setPlaying(false) }
      else { setReplay(false); setCursor(null) }
      clearFocus()
    })().catch(e => setError(String(e.message ?? e)))
  }, [focus, symbol, tf]) // eslint-disable-line react-hooks/exhaustive-deps

  // live forming candle every 2 s, so the chart moves with the market (display only)
  const [liveBar, setLiveBar] = useState<{ time: number; open: number; high: number; low: number; close: number; closes_at: number } | null>(null)
  useEffect(() => {
    setLiveBar(null)
    if (replay || pickStart) return
    let stop = false
    const poll = () => live.candle(symbol, tf).then(b => { if (!stop) setLiveBar(b) }).catch(() => {})
    poll()
    const h = setInterval(poll, 2000)
    return () => { stop = true; clearInterval(h) }
  }, [symbol, tf, replay, pickStart])
  const [, setNow] = useState(0)
  useEffect(() => { const h = setInterval(() => setNow(n => n + 1), 1000); return () => clearInterval(h) }, [])

  // follow the live market: append newly closed candles (never while replaying)
  useEffect(() => {
    if (replay || pickStart || !bars) return
    const h = setInterval(async () => {
      try {
        const probe = await api.bars(symbol, tf, 1)
        if (probe.total <= bars.total) return
        // keep the same first bar so the chart doesn't jump
        setBars(await api.bars(symbol, tf, probe.total - bars.first_index))
      } catch { /* backend busy: try again next time */ }
    }, 30000)
    return () => clearInterval(h)
  }, [bars, replay, pickStart, symbol, tf])

  // overlays + key zones as of the visible end (debounced during replay)
  const reqId = useRef(0)
  useEffect(() => {
    if (!bars) return
    const id = ++reqId.current
    const h = setTimeout(async () => {
      try {
        const [ov, kz] = await Promise.all([
          api.overlays(symbol, tf, bars.first_index, visibleEnd),
          api.zones(symbol, tf, replay ? visibleEnd : undefined),
        ])
        if (id !== reqId.current) return
        setOverlays(ov); setZones(kz)
      } catch (e) { if (id === reqId.current) setError((e as Error).message) }
    }, replay ? 120 : 0)
    return () => clearTimeout(h)
  }, [bars, visibleEnd, replay, symbol, tf])

  // replay playback
  useEffect(() => {
    if (!playing || !replay) return
    const h = setInterval(() => {
      setCursor(c => {
        if (c === null) return c
        if (c >= lastIndex) { setPlaying(false); return c }
        return c + 1
      })
    }, 1000 / speed)
    return () => clearInterval(h)
  }, [playing, replay, speed, lastIndex])

  const onBarClick = useCallback((g: number) => {
    if (pickStart) {
      setPickStart(false); setReplay(true); setCursor(g); setPlaying(false); setSelZone(null)
      return
    }
    if (g > visibleEnd) return
    api.barContext(symbol, tf, g).then(setInspect).catch(() => {})
  }, [pickStart, visibleEnd, symbol, tf])

  const startReplay = () => { setPickStart(true); setPlaying(false) }
  const exitReplay = () => { setReplay(false); setCursor(null); setPlaying(false); setMarker(null); load() }

  const symOptions = meta?.symbols ?? []
  const tfOptions = meta?.timeframes ?? ['M5', 'M15', 'H1', 'H4', 'D1']
  const zoneList = useMemo(() => zones ? [...zones.buy_zones, ...zones.sell_zones] : [], [zones])

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Chart explorer</h1>
          <p>Market structure, imbalances and liquidity detected bar by bar, using only information that existed at the time.</p>
        </div>
      </div>

      <div className="toolbar">
        <select value={symbol} onChange={e => setSymbol(e.target.value)} aria-label="Instrument">
          {symOptions.map(s => <option key={s.id} value={s.id}>{s.id} · {s.label}</option>)}
        </select>
        <div className="seg" role="group" aria-label="Timeframe">
          {tfOptions.map(t => <button key={t} className={t === tf ? 'on' : ''} onClick={() => setTf(t)}>{t}</button>)}
        </div>
        <span className="grow" />
        {LAYER_LABELS.map(([k, label]) => (
          <button key={k} className={`btn sm ${layers[k] ? 'on' : ''}`} onClick={() => setLayers(l => ({ ...l, [k]: !l[k] }))}>{label}</button>
        ))}
      </div>

      {error && <div className="error">{error}</div>}

      <div className="chart-layout">
        <div className="panel" style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <PriceChart bars={bars} showCount={showCount} overlays={overlays} layers={layers} keyZones={zones}
            selectedZone={selZone} marker={marker} theme={theme} liveBar={liveBar} onBarClick={onBarClick} />
          <div className="replay-bar">
            {!replay && !pickStart && <button className="btn sm" onClick={startReplay} disabled={!bars}>▶ Replay from a bar…</button>}
            {pickStart && <><span className="chip accent">Click the bar where replay should start</span><button className="btn sm ghost" onClick={() => setPickStart(false)}>Cancel</button></>}
            {replay && cursor !== null && (
              <>
                <span className="chip warn">REPLAY</span>
                <button className="btn sm" onClick={() => setCursor(c => Math.max(bars!.first_index + 50, (c ?? 0) - 1))}>◀ Step</button>
                <button className="btn sm primary" onClick={() => setPlaying(p => !p)}>{playing ? '❚❚ Pause' : '▶ Play'}</button>
                <button className="btn sm" onClick={() => setCursor(c => Math.min(lastIndex, (c ?? 0) + 1))}>Step ▶</button>
                <button className="btn sm" onClick={() => setCursor(c => Math.min(lastIndex, (c ?? 0) + 10))}>+10</button>
                <select value={speed} onChange={e => setSpeed(Number(e.target.value))} aria-label="Speed">
                  {[1, 2, 4, 8, 16].map(s => <option key={s} value={s}>{s} bars/s</option>)}
                </select>
                <span className="small muted num">{bars ? fmt.local(bars.time[cursor - bars.first_index]) : ''} {TZ_LABEL}</span>
                <span className="grow" />
                <button className="btn sm ghost" onClick={exitReplay}>Exit replay</button>
              </>
            )}
            {!replay && !pickStart && liveBar && (
              <span className="chip bull num" title="Forming candle from MT5. Analysis and signals only use closed candles.">
                LIVE {fmt.price(liveBar.close)} · closes in {Math.max(0, Math.floor((liveBar.closes_at - Date.now() / 1000) / 60))}:{String(Math.max(0, Math.floor((liveBar.closes_at - Date.now() / 1000) % 60))).padStart(2, '0')}
              </span>
            )}
            {!replay && !pickStart && <span className="small faint">Click any bar to see which conditions were active on it.</span>}
            {loading && <span className="spinner" />}
          </div>
        </div>

        <div className="side">
          <div className="panel">
            <div className="panel-head">
              <h3>Key trade zones</h3>
              {zones && <span className="small faint num">as of {fmt.local(zones.time)}</span>}
            </div>
            <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {!zones && <span className="muted small">Load data to see zones.</span>}
              {zones && zoneList.length === 0 && <span className="muted small">No active, unmitigated zones near price.</span>}
              {zoneList.map(z => {
                const sel = selZone?.start_i === z.start_i && selZone?.kind === z.kind
                return (
                  <div key={z.kind + z.start_i} className={`zone-card ${sel ? 'sel' : ''}`} onClick={() => setSelZone(sel ? null : z)}>
                    <div className="row">
                      <span className={`chip ${z.direction === 'long' ? 'bull' : 'bear'}`}>{z.direction === 'long' ? 'BUY' : 'SELL'}</span>
                      <b style={{ fontSize: 13 }}>{z.label}</b>
                      <span className="grow" />
                      <span className="chip" title="Evidence grade of this zone type on this market">Grade {z.stats.grade}</span>
                    </div>
                    <div className="lv num">
                      <span className="muted">Entry</span><span>{fmt.price(z.entry)}</span><span className="faint">{fmt.num(z.distance_atr, 1)} ATR away</span>
                      <span className="muted">Stop</span><span>{fmt.price(z.stop)}</span><span />
                      {z.targets.slice(0, 2).map((t, k) => (
                        <FragmentRow key={k} a={`TP${k + 1}`} b={fmt.price(t.price)} c={`${fmt.num(t.rr, 1)}R · ${t.kind}`} />
                      ))}
                    </div>
                    <div className="small muted">
                      History: <span className="num">{fmt.int(z.stats.n)}</span> retests · win (2R) <span className="num">{fmt.pct(z.stats.win_rate, 0)}</span> · avg <span className={`num ${(z.stats.avg_r ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmt.r(z.stats.avg_r)}</span> · OOS <span className="num">{fmt.r(z.stats.oos_avg_r)}</span>
                    </div>
                    {z.confluence.length > 0 && <div className="row">{z.confluence.map(c => <span key={c} className="chip">{c}</span>)}</div>}
                  </div>
                )
              })}
              {zones && <span className="small faint">{zones.disclaimer} Click a zone to draw its entry, stop and liquidity targets.</span>}
            </div>
          </div>

          {zones && (
            <div className="panel">
              <div className="panel-head"><h3>Liquidity (exit zones)</h3></div>
              <div className="panel-body">
                <table className="t num">
                  <tbody>
                    {zones.liquidity.above.slice(0, 4).reverse().map((l, k) => (
                      <tr key={'a' + k}><td className="neg">▲</td><td>{fmt.price(l.price)}</td><td className="muted">{l.kind}</td></tr>
                    ))}
                    <tr><td>•</td><td><b>{fmt.price(zones.close)}</b></td><td className="muted">price</td></tr>
                    {zones.liquidity.below.slice(0, 4).map((l, k) => (
                      <tr key={'b' + k}><td className="pos">▼</td><td>{fmt.price(l.price)}</td><td className="muted">{l.kind}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {inspect && (
            <div className="panel">
              <div className="panel-head"><h3>Bar inspector</h3><button className="btn sm ghost" onClick={() => setInspect(null)}>✕</button></div>
              <div className="panel-body">
                <div className="small muted num" style={{ marginBottom: 8 }}>{fmt.date(inspect.time)} UTC · {inspect.conditions.length} conditions true at close</div>
                <div className="row">
                  {inspect.conditions.map(c => <span key={c.id} className={`chip ${c.bias === 'bull' ? 'bull' : c.bias === 'bear' ? 'bear' : ''}`}>{c.label}</span>)}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function FragmentRow({ a, b, c }: { a: string; b: string; c: string }) {
  return <><span className="muted">{a}</span><span>{b}</span><span className="faint">{c}</span></>
}
