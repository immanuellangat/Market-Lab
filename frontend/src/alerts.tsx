import { useEffect, useRef, useState } from 'react'
import { live, type PriceAlert, type Signal } from './api'
import { fmt } from './state'

const KEY = 'lastSignalId'
const POLL_MS = 15000

function readLast(): number | null {
  try { const v = localStorage.getItem(KEY); return v === null ? null : Number(v) } catch { return null }
}
function writeLast(id: number) { try { localStorage.setItem(KEY, String(id)) } catch { /* ignore */ } }

function beep() {
  try {
    const ctx = new AudioContext()
    ;[0, 0.18].forEach((t, k) => {
      const o = ctx.createOscillator(), g = ctx.createGain()
      o.frequency.value = k ? 1046 : 784
      g.gain.setValueAtTime(0.0001, ctx.currentTime + t)
      g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + t + 0.02)
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t + 0.16)
      o.connect(g).connect(ctx.destination)
      o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.17)
    })
  } catch { /* audio unavailable */ }
}

export const notifyPermission = () => (typeof Notification === 'undefined' ? 'unsupported' : Notification.permission)
export const requestNotify = () => (typeof Notification === 'undefined' ? Promise.resolve('unsupported') : Notification.requestPermission())

export function signalTitle(s: Signal) {
  const late = s.payload.late_bars
  return `${late ? `LATE (${late} candle${late === 1 ? '' : 's'} ago) · ` : ''}${s.direction === 'long' ? 'BUY' : 'SELL'} ${s.symbol} ${s.tf} — ${s.payload.name}`
}

/** Polls for new signals; returns the unseen ones for in-app toasts. */
export function useSignalAlerts() {
  const [toasts, setToasts] = useState<Signal[]>([])
  const last = useRef<number | null>(readLast())

  useEffect(() => {
    let stop = false
    const poll = async () => {
      try {
        const rows = await live.signals(last.current ?? 0, 50)
        if (stop || !rows.length) return
        const maxId = Math.max(...rows.map(r => r.id))
        if (last.current === null) { last.current = maxId; writeLast(maxId); return } // first run: don't alert old signals
        last.current = maxId; writeLast(maxId)
        const fresh = rows.sort((a, b) => a.id - b.id)
        setToasts(t => [...[...fresh].reverse(), ...t].slice(0, 5))
        beep()
        if (notifyPermission() === 'granted') {
          for (const s of fresh) {
            const ev = s.payload.evidence
            new Notification(signalTitle(s), {
              body: `Entry ≈ ${fmt.price(s.entry)} · SL ${fmt.price(s.stop)} · TP ${fmt.price(s.target)}${ev ? ` · grade ${ev.grade}` : ''}`,
              tag: `signal-${s.id}`,
            })
          }
        }
      } catch { /* backend busy or offline: try again next poll */ }
    }
    poll()
    const h = setInterval(poll, POLL_MS)
    return () => { stop = true; clearInterval(h) }
  }, [])

  return { toasts, dismiss: (id: number) => setToasts(t => t.filter(s => s.id !== id)) }
}

const PA_KEY = 'lastPriceAlertAt'

export function priceAlertTitle(a: PriceAlert) {
  return `${a.late ? 'LATE · ' : ''}🔔 ${a.symbol} reached ${a.label}`
}

/** Polls for price alerts that fired; returns the unseen ones for in-app toasts. */
export function usePriceAlertToasts() {
  const [toasts, setToasts] = useState<PriceAlert[]>([])
  const since = useRef<number | null>((() => { try { const v = localStorage.getItem(PA_KEY); return v === null ? null : Number(v) } catch { return null } })())

  useEffect(() => {
    let stop = false
    const poll = async () => {
      try {
        const first = since.current === null
        const rows = await live.priceAlerts(undefined, since.current ?? Math.floor(Date.now() / 1000) - 60)
        const fired = rows.filter(r => r.status === 'triggered' && r.triggered_at)
        if (stop) return
        const latest = Math.max(since.current ?? 0, ...fired.map(r => r.triggered_at!), first ? Math.floor(Date.now() / 1000) : 0)
        since.current = latest
        try { localStorage.setItem(PA_KEY, String(latest)) } catch { /* ignore */ }
        if (first || !fired.length) return
        setToasts(t => [...fired, ...t].slice(0, 5))
        beep()
        if (notifyPermission() === 'granted') {
          for (const a of fired) new Notification(priceAlertTitle(a), { body: `Price ≈ ${fmt.price(a.trigger_price)}`, tag: `pa-${a.id}` })
        }
      } catch { /* backend busy or offline: try again next poll */ }
    }
    poll()
    const h = setInterval(poll, POLL_MS)
    return () => { stop = true; clearInterval(h) }
  }, [])

  return { paToasts: toasts, dismissPa: (id: number) => setToasts(t => t.filter(a => a.id !== id)) }
}
