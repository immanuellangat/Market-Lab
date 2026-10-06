import type {
  IChartApi, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive,
  SeriesAttachedParameter, SeriesType, Time,
} from 'lightweight-charts'
import type { CanvasRenderingTarget2D } from 'fancy-canvas'

/** x positions are *logical* bar indexes into the series data; 'right' extends to the pane edge. */
export interface Rect { l1: number; l2: number | 'right'; top: number; bottom: number; fill: string; stroke?: string; label?: string; labelColor?: string }
export interface Seg { l1: number; l2: number | 'right'; price: number; color: string; dash?: boolean; width?: number; label?: string }
export interface Shapes { rects: Rect[]; segs: Seg[] }

class Renderer implements IPrimitivePaneRenderer {
  private src: OverlayPrimitive
  constructor(src: OverlayPrimitive) { this.src = src }

  draw(target: CanvasRenderingTarget2D) {
    const chart = this.src.chart
    const series = this.src.series
    if (!chart || !series) return
    const ts = chart.timeScale()
    const { rects, segs } = this.src.shapes
    target.useBitmapCoordinateSpace(scope => {
      const ctx = scope.context
      const hr = scope.horizontalPixelRatio
      const vr = scope.verticalPixelRatio
      const W = scope.bitmapSize.width
      const x = (l: number | 'right') => (l === 'right' ? W : (ts.logicalToCoordinate(l as never) ?? NaN) * hr)
      ctx.font = `${Math.round(10 * vr)}px system-ui, sans-serif`
      ctx.textBaseline = 'top'

      for (const r of rects) {
        const x1 = x(r.l1), x2 = x(r.l2)
        const y1 = series.priceToCoordinate(r.top), y2 = series.priceToCoordinate(r.bottom)
        if (!isFinite(x1) || !isFinite(x2) || y1 === null || y2 === null) continue
        if (x2 < 0 || x1 > W) continue
        const top = Math.min(y1, y2) * vr, h = Math.max(1, Math.abs(y2 - y1) * vr)
        ctx.fillStyle = r.fill
        ctx.fillRect(x1, top, x2 - x1, h)
        if (r.stroke) {
          ctx.strokeStyle = r.stroke
          ctx.lineWidth = Math.max(1, hr)
          ctx.strokeRect(x1, top, x2 - x1, h)
        }
        if (r.label && x2 - x1 > 40 * hr) {
          ctx.fillStyle = r.labelColor ?? r.stroke ?? '#999'
          ctx.fillText(r.label, Math.max(x1, 0) + 4 * hr, top + 2 * vr)
        }
      }
      for (const s of segs) {
        const x1 = x(s.l1), x2 = x(s.l2)
        const y = series.priceToCoordinate(s.price)
        if (!isFinite(x1) || !isFinite(x2) || y === null) continue
        if (x2 < 0 || x1 > W) continue
        const yy = Math.round(y * vr) + 0.5
        ctx.strokeStyle = s.color
        ctx.lineWidth = (s.width ?? 1) * hr
        ctx.setLineDash(s.dash ? [5 * hr, 4 * hr] : [])
        ctx.beginPath()
        ctx.moveTo(x1, yy)
        ctx.lineTo(x2, yy)
        ctx.stroke()
        ctx.setLineDash([])
        if (s.label) {
          ctx.fillStyle = s.color
          const tw = ctx.measureText(s.label).width
          const lx = s.l2 === 'right' ? x2 - tw - 60 * hr : Math.max(x1, 0) + 4 * hr
          ctx.fillText(s.label, lx, yy - 13 * vr)
        }
      }
    })
  }
}

class PaneView implements IPrimitivePaneView {
  private r: Renderer
  constructor(src: OverlayPrimitive) { this.r = new Renderer(src) }
  zOrder() { return 'bottom' as const }
  renderer() { return this.r }
}

export class OverlayPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  shapes: Shapes = { rects: [], segs: [] }
  private requestUpdate: (() => void) | null = null
  private views = [new PaneView(this)]

  attached(p: SeriesAttachedParameter<Time>) {
    this.chart = p.chart as IChartApi
    this.series = p.series
    this.requestUpdate = p.requestUpdate
  }
  detached() { this.chart = null; this.series = null; this.requestUpdate = null }
  updateAllViews() { /* coordinates are resolved at draw time */ }
  paneViews() { return this.views }
  setShapes(s: Shapes) { this.shapes = s; this.requestUpdate?.() }
}
