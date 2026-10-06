import { useEffect, useRef } from 'react'
import { ColorType, LineSeries, createChart, type IChartApi, type UTCTimestamp } from 'lightweight-charts'

export interface LineDef { points: { time: number; value: number }[]; color: string; title?: string }

/** Small time-series line chart (equity curves, rolling expectancy). */
export default function MiniLine({ lines, theme, zeroLine = true }: { lines: LineDef[]; theme: string; zeroLine?: boolean }) {
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)

  useEffect(() => {
    const s = getComputedStyle(document.documentElement)
    const g = (n: string) => s.getPropertyValue(n).trim()
    const c = createChart(box.current!, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: g('--muted'), fontSize: 11, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: g('--border') } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      handleScroll: false, handleScale: false,
    })
    chart.current = c
    for (const l of lines) {
      // de-duplicate identical timestamps (several trades on one bar time is impossible, but be safe)
      const seen = new Set<number>()
      const data = l.points.filter(p => (seen.has(p.time) ? false : (seen.add(p.time), true)))
        .map(p => ({ time: p.time as UTCTimestamp, value: p.value }))
      const ser = c.addSeries(LineSeries, { color: l.color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: l.title })
      ser.setData(data)
      if (zeroLine && l === lines[0]) ser.createPriceLine({ price: 0, color: g('--faint'), lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' })
    }
    c.timeScale().fitContent()
    return () => c.remove()
  }, [lines, theme, zeroLine])

  return <div ref={box} className="mini-chart" />
}
