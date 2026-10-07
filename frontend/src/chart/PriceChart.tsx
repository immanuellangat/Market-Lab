import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries, ColorType, CrosshairMode, LineSeries, LineStyle, LineType, createChart, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type ISeriesMarkersPluginApi, type SeriesMarker, type Time, type UTCTimestamp,
} from 'lightweight-charts'
import type { Bars, KeyZones, Overlays, QuickStats, SetupView, TradeZone, Zone } from '../api'
import { TZ_LABEL, TZ_OFFSET, fmt } from '../state'
import { OverlayPrimitive, type Rect, type Seg } from './overlayPrimitive'

export interface Layers { fvg: boolean; ob: boolean; spent: boolean; structure: boolean; swings: boolean; liquidity: boolean; levels: boolean; zones: boolean }

interface Props {
  bars: Bars | null
  /** number of bars (from the start of `bars`) to show; used by replay to hide the future */
  showCount: number
  overlays: Overlays | null
  layers: Layers
  keyZones: KeyZones | null
  selectedZone: TradeZone | null
  marker: { index: number; text: string; dir?: string } | null
  theme: string
  /** the still-forming candle from MT5 (display only) */
  liveBar?: { time: number; open: number; high: number; low: number; close: number } | null
  /** historical first-retest record per zone type (fvg_bull, ob_bear, ...) for tooltips */
  zoneStats?: Record<string, QuickStats> | null
  onBarClick?: (globalIndex: number) => void
  /** a setup drawn on the chart: condition lines + past occurrences */
  setupView?: SetupView | null
  showOccurrences?: boolean
}

// Lightweight Charts renders timestamps as UTC; shifting by the local offset displays local time.
const ct = (t: number) => (t + TZ_OFFSET) as UTCTimestamp

function tokens() {
  const s = getComputedStyle(document.documentElement)
  const g = (n: string) => s.getPropertyValue(n).trim()
  return { bg: g('--panel'), text: g('--muted'), grid: g('--border'), bull: g('--bull'), bear: g('--bear'),
    accent: g('--accent'), warn: g('--warn'), faint: g('--faint') }
}
const alpha = (hex: string, a: number) => {
  const h = hex.replace('#', '')
  if (h.length !== 6) return hex
  const n = parseInt(h, 16)
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`
}

export default function PriceChart({ bars, showCount, overlays, layers, keyZones, selectedZone, marker, theme, liveBar, zoneStats, onBarClick, setupView, showOccurrences = true }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const candles = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const levelSeries = useRef<Record<string, ISeriesApi<'Line'>>>({})
  const prim = useRef(new OverlayPrimitive())
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const clickRef = useRef(onBarClick)
  const barsRef = useRef(bars)
  const [legend, setLegend] = useState<string>('')
  const [hover, setHover] = useState<{ zone: Zone; x: number; y: number } | null>(null)
  const zoneById = useRef<Record<string, Zone>>({})
  clickRef.current = onBarClick
  barsRef.current = bars

  // create chart once
  useEffect(() => {
    const el = box.current!
    const c = createChart(el, {
      autoSize: true,
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 8 },
      rightPriceScale: { scaleMargins: { top: 0.08, bottom: 0.08 } },
    })
    const s = c.addSeries(CandlestickSeries, {})
    s.attachPrimitive(prim.current)
    markers.current = createSeriesMarkers(s, [])
    for (const k of ['pdh', 'pdl', 'asia_high', 'asia_low']) {
      levelSeries.current[k] = c.addSeries(LineSeries, {
        lineWidth: 1, lineType: LineType.WithSteps, lineStyle: k.startsWith('asia') ? LineStyle.Dotted : LineStyle.Dashed,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      })
    }
    c.subscribeClick(p => {
      const b = barsRef.current
      if (!b || p.logical === undefined || p.logical === null) return
      clickRef.current?.(b.first_index + Math.round(p.logical))
    })
    c.subscribeCrosshairMove(p => {
      const b = barsRef.current
      const hitId = p.point ? prim.current.hitTestAt(p.point.x, p.point.y) : null
      const hz = hitId ? zoneById.current[hitId] : undefined
      setHover(hz && p.point ? { zone: hz, x: p.point.x, y: p.point.y } : null)
      if (!b || p.logical === undefined || p.logical === null) { setLegend(''); return }
      const i = Math.round(p.logical)
      if (i < 0 || i >= b.time.length) { setLegend(''); return }
      setLegend(`${fmt.local(b.time[i])} ${TZ_LABEL}  O ${fmt.price(b.open[i])}  H ${fmt.price(b.high[i])}  L ${fmt.price(b.low[i])}  C ${fmt.price(b.close[i])}`)
    })
    chart.current = c
    candles.current = s
    return () => { c.remove(); chart.current = null }
  }, [])

  // theme
  useEffect(() => {
    const t = tokens()
    chart.current?.applyOptions({
      layout: { background: { type: ColorType.Solid, color: t.bg }, textColor: t.text, fontSize: 11 },
      grid: { vertLines: { color: alpha(t.grid, 0.5) }, horzLines: { color: alpha(t.grid, 0.5) } },
      rightPriceScale: { borderColor: t.grid }, timeScale: { borderColor: t.grid },
    })
    candles.current?.applyOptions({ upColor: t.bull, downColor: t.bear, borderVisible: false, wickUpColor: t.bull, wickDownColor: t.bear })
    levelSeries.current.pdh?.applyOptions({ color: alpha(t.bear, 0.7) })
    levelSeries.current.pdl?.applyOptions({ color: alpha(t.bull, 0.7) })
    levelSeries.current.asia_high?.applyOptions({ color: alpha(t.warn, 0.8) })
    levelSeries.current.asia_low?.applyOptions({ color: alpha(t.warn, 0.8) })
  }, [theme])

  // candles + levels
  const prevKey = useRef('')
  useEffect(() => {
    if (!bars || !candles.current) return
    const n = Math.min(showCount, bars.time.length)
    const data = []
    for (let i = 0; i < n; i++) {
      data.push({ time: ct(bars.time[i]), open: bars.open[i], high: bars.high[i], low: bars.low[i], close: bars.close[i] })
    }
    candles.current.setData(data)
    const d = bars.close[n - 1]
    const prec = d >= 1000 ? 2 : d >= 50 ? 3 : 5
    candles.current.applyOptions({ priceFormat: { type: 'price', precision: prec, minMove: Math.pow(10, -prec) } })
    for (const [k, s] of Object.entries(levelSeries.current)) {
      const arr = bars.levels[k]
      if (!arr || !layers.levels) { s.setData([]); continue }
      const ld = []
      for (let i = 0; i < n; i++) {
        const v = arr[i]
        ld.push(v === null ? { time: ct(bars.time[i]) } : { time: ct(bars.time[i]), value: v })
      }
      s.setData(ld)
    }
    const key = `${bars.symbol}|${bars.tf}|${bars.first_index}`
    if (key !== prevKey.current) {
      prevKey.current = key
      const ts = chart.current!.timeScale()
      ts.setVisibleLogicalRange({ from: Math.max(0, n - 180), to: n + 8 })
    }
  }, [bars, showCount, layers.levels])

  // overlays -> shapes and markers
  useEffect(() => {
    if (!bars) return
    const t = tokens()
    const off = bars.first_index
    const last = Math.min(showCount, bars.time.length) - 1
    const L = (g: number) => g - off
    const rects: Rect[] = []
    const segs: Seg[] = []
    const mk: SeriesMarker<Time>[] = []
    const timeOf = (g: number) => ct(bars.time[Math.max(0, Math.min(L(g), last))])

    if (overlays) {
      const byId: Record<string, Zone> = {}
      overlays.zones.forEach((z, k) => {
        const isFvg = z.kind.startsWith('fvg')
        if ((isFvg && !layers.fvg) || (!isFvg && !layers.ob)) return
        if (z.end !== null && z.end < off) return
        const fresh = z.end === null
        if (!fresh && !layers.spent) return
        const bull = z.kind.endsWith('bull')
        const col = bull ? t.bull : t.bear
        const id = `z${k}`
        byId[id] = z
        // order blocks: solid border; FVGs: dashed border. Green = bullish, red = bearish.
        rects.push({
          l1: L(z.start), l2: fresh ? last + 0.5 : L(z.end!),
          top: z.top, bottom: z.bottom,
          fill: alpha(col, fresh ? (isFvg ? 0.12 : 0.18) : 0.05),
          stroke: alpha(col, fresh ? (isFvg ? 0.65 : 0.9) : 0.25),
          dash: isFvg,
          label: fresh ? (isFvg ? 'FVG' : 'OB') : undefined, labelColor: col, id,
        })
      })
      zoneById.current = byId
      if (layers.structure) {
        for (const e of overlays.events) {
          if (e.i < off) continue
          if (e.kind.startsWith('bos') || e.kind.startsWith('choch')) {
            const bull = e.kind.endsWith('bull')
            segs.push({ l1: L(Math.max(e.ref_i, off)), l2: L(e.i), price: e.level, color: bull ? t.bull : t.bear, dash: e.kind.startsWith('bos'),
              label: e.kind.startsWith('bos') ? 'BOS' : 'CHoCH' })
          } else if (e.kind.startsWith('div')) {
            const bull = e.kind === 'div_bull'
            mk.push({ time: timeOf(e.i), position: bull ? 'belowBar' : 'aboveBar', shape: 'circle', color: t.warn, text: 'Div', size: 0.6 })
          }
        }
      }
      if (layers.liquidity) {
        for (const e of overlays.events) {
          if (e.i < off) continue
          if (e.kind === 'sweep_low') mk.push({ time: timeOf(e.i), position: 'belowBar', shape: 'arrowUp', color: t.bull, text: 'Sweep', size: 0.8 })
          if (e.kind === 'sweep_high') mk.push({ time: timeOf(e.i), position: 'aboveBar', shape: 'arrowDown', color: t.bear, text: 'Sweep', size: 0.8 })
        }
        for (const p of overlays.pools) {
          if (p.end !== null && p.end < off) continue
          segs.push({ l1: L(Math.max(p.start, off)), l2: p.end === null ? last : L(p.end), price: p.level, color: alpha(t.warn, 0.9),
            dash: true, label: p.kind === 'eqh' ? 'EQH' : 'EQL' })
        }
      }
      if (layers.swings) {
        for (const s of overlays.swings) {
          if (s.i < off || s.kind.length < 2) continue
          mk.push({ time: timeOf(s.i), position: s.side === 'high' ? 'aboveBar' : 'belowBar', shape: 'circle', size: 0.1,
            color: t.faint, text: s.kind })
        }
      }
    }

    if (layers.zones && keyZones) {
      const zs = [...keyZones.buy_zones, ...keyZones.sell_zones]
      for (const z of zs) {
        const bull = z.direction === 'long'
        const sel = selectedZone && selectedZone.start_i === z.start_i && selectedZone.kind === z.kind
        rects.push({ l1: L(Math.max(z.start_i, off)), l2: 'right', top: z.top, bottom: z.bottom,
          fill: alpha(bull ? t.bull : t.bear, sel ? 0.28 : 0.14), stroke: bull ? t.bull : t.bear,
          label: `${bull ? 'BUY' : 'SELL'} ZONE · ${z.label} · ${z.stats.grade}`, labelColor: bull ? t.bull : t.bear })
      }
    }
    if (selectedZone) {
      const z = selectedZone
      const start = L(Math.max(z.start_i, off))
      segs.push({ l1: start, l2: 'right', price: z.entry, color: t.accent, width: 1.5, label: `Entry ${fmt.price(z.entry)}` })
      segs.push({ l1: start, l2: 'right', price: z.stop, color: t.bear, width: 1.5, dash: true, label: `Stop ${fmt.price(z.stop)}` })
      z.targets.forEach((x, k) => segs.push({ l1: start, l2: 'right', price: x.price, color: t.bull, dash: true,
        label: `TP${k + 1} ${x.kind} · ${fmt.num(x.rr, 1)}R` }))
      z.stat_targets.forEach(x => segs.push({ l1: start, l2: 'right', price: x.price, color: alpha(t.accent, 0.8), dash: true,
        label: `${x.kind} · ${fmt.num(x.rr, 1)}R` }))
    }
    if (setupView) {
      const sv = setupView
      // conditions with a real price level: a line at that level
      const withLevel = sv.checklist.filter(c => c.level !== null)
      withLevel.forEach(c => segs.push({ l1: Math.max(0, last - 80), l2: 'right', price: c.level!, width: 1.5,
        color: c.met ? t.bull : t.warn, dash: !c.met,
        label: `${c.met ? '✓' : '✗'} ${c.label} · ${c.level_label} ${fmt.price(c.level)}` }))
      // conditions without a price (day, session, volatility, higher-timeframe state...): an imaginary
      // marker line just above price, stacked so the labels never overlap
      // conditions about a zone (FVG / order block): the nearest active zone as a box, i.e. where the retest would happen
      sv.checklist.filter(c => c.zone).forEach(c => {
        const z = c.zone!
        let lo = 0, hi = last
        while (lo < hi) { const mid = (lo + hi) >> 1; if (bars.time[mid] < z.start_time) lo = mid + 1; else hi = mid }
        const col = c.met ? t.bull : t.warn
        rects.push({ l1: Math.max(0, lo), l2: 'right', top: z.top, bottom: z.bottom, fill: alpha(col, 0.16), stroke: col, dash: !c.met,
          label: `${c.met ? '✓' : '✗'} ${c.label} · ${z.label}`, labelColor: col })
      })
      const noLevel = sv.checklist.filter(c => c.level === null && !c.zone)
      noLevel.forEach((c, k) => segs.push({ l1: Math.max(0, last - 25), l2: 'right', price: sv.close + (0.8 + 0.55 * k) * sv.atr,
        color: c.met ? t.bull : t.warn, dash: true, width: 1.5,
        label: `${c.met ? '✓' : '✗'} ${c.label}` }))
      if (showOccurrences) {
        const times = bars.time
        const seen = sv.occurrences.filter(o => o.time >= times[0] && o.time <= times[last])
        seen.slice(0, 150).forEach((o, k) => {
          // binary search the candle of this occurrence
          let lo = 0, hi = last
          while (lo < hi) { const mid = (lo + hi) >> 1; if (times[mid] < o.time) lo = mid + 1; else hi = mid }
          if (times[lo] !== o.time) return
          const win = o.r > 0
          mk.push({ time: ct(times[lo]), position: sv.direction === 'short' ? 'aboveBar' : 'belowBar',
            shape: sv.direction === 'short' ? 'arrowDown' : 'arrowUp', color: win ? t.bull : t.bear, size: 1,
            text: k < 40 ? `${o.r >= 0 ? '+' : ''}${o.r.toFixed(1)}R` : undefined })
        })
      }
    }
    if (marker && marker.index >= off && marker.index - off <= last) {
      mk.push({ time: timeOf(marker.index), position: marker.dir === 'short' ? 'aboveBar' : 'belowBar',
        shape: marker.dir === 'short' ? 'arrowDown' : 'arrowUp', color: t.accent, text: marker.text, size: 1.6 })
    }
    mk.sort((a, b) => (a.time as number) - (b.time as number))
    markers.current?.setMarkers(mk)
    prim.current.setShapes({ rects, segs })
  }, [bars, showCount, overlays, layers, keyZones, selectedZone, marker, theme, setupView, showOccurrences])

  // live forming candle: append/update after the last closed bar (never during replay)
  useEffect(() => {
    if (!liveBar || !bars || !candles.current) return
    if (showCount < bars.time.length) return
    if (liveBar.time <= bars.time[bars.time.length - 1]) return
    try {
      candles.current.update({ time: ct(liveBar.time), open: liveBar.open, high: liveBar.high, low: liveBar.low, close: liveBar.close })
    } catch { /* out-of-order update after a reload: ignored until the next poll */ }
  }, [liveBar, bars, showCount])

  return (
    <div className="chart-wrap">
      <div className="chart-legend num">{legend}</div>
      {hover && bars && <ZoneTip h={hover} bars={bars} stats={zoneStats?.[hover.zone.kind]} />}
      <div ref={box} className="chart-box" />
    </div>
  )
}

const STATUS_TIP: Record<string, string> = {
  fresh: 'price has not returned yet',
  'partly filled': 'price has entered the gap but not filled it',
  tapped: 'spent: price returned into it',
  filled: 'spent: the gap was filled',
  broken: 'spent: a candle closed through it',
  expired: 'spent: too old',
}

function ZoneTip({ h, bars, stats }: { h: { zone: Zone; x: number; y: number }; bars: Bars; stats?: QuickStats }) {
  const z = h.zone
  const isFvg = z.kind.startsWith('fvg')
  const bull = z.kind.endsWith('bull')
  const i = z.formed - bars.first_index
  const atr = i >= 0 && i < bars.atr.length ? bars.atr[i] : null
  const t = (g: number) => {
    const k = g - bars.first_index
    return k >= 0 && k < bars.time.length ? fmt.local(bars.time[k]) : 'earlier'
  }
  const fresh = z.end === null
  return (
    <div className="zone-tip" style={{ left: h.x + 16, top: Math.max(4, h.y - 10) }}>
      <div className="row" style={{ gap: 6 }}>
        <b style={{ color: bull ? 'var(--bull)' : 'var(--bear)' }}>{bull ? 'Bullish' : 'Bearish'} {isFvg ? 'FVG' : 'order block'}</b>
        <span className={`chip ${fresh ? 'bull' : ''}`}>{z.status}</span>
      </div>
      <div className="muted">{STATUS_TIP[z.status] ?? ''}{!fresh && ` (${t(z.end!)})`}</div>
      <div className="num">Formed {t(z.formed)} · {fmt.price(z.bottom)}–{fmt.price(z.top)}{atr ? ` · ${((z.top - z.bottom) / atr).toFixed(1)} ATR tall` : ''}</div>
      {stats && stats.n > 0 && (
        <div className="num" style={{ marginTop: 4 }}>
          History ({bars.symbol} {bars.tf}): {fmt.int(stats.n)} first retests · win {fmt.pct(stats.win_rate, 0)} ·{' '}
          <span className={(stats.avg_r ?? 0) >= 0 ? 'pos' : 'neg'}>{fmt.r(stats.avg_r)}</span> (unseen {fmt.r(stats.oos_avg_r)}) · grade {stats.grade}
        </div>
      )}
    </div>
  )
}
