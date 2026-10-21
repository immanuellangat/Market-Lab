import { useEffect, useState } from 'react'
import { live, type TelegramCfg } from '../api'
import { notifyPermission, requestNotify } from '../alerts'

export default function SettingsPage() {
  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Settings</h1>
          <p>Where alerts reach you. Signals, price alerts, edge changes and connection problems are always kept on the Notifications page as well.</p>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 12, alignItems: 'start' }}>
        <TelegramPanel />
        <DesktopPanel />
      </div>
    </div>
  )
}

function DesktopPanel() {
  const [perm, setPerm] = useState(notifyPermission())
  return (
    <div className="panel">
      <div className="panel-head"><h3>Desktop alerts</h3>{perm === 'granted' && <span className="chip accent">on</span>}</div>
      <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div className="small muted">
          While Market Lab is open, new signals and price alerts pop up inside the app with a sound. Desktop alerts also show
          a system notification when the window is in the background.
        </div>
        {perm === 'unsupported' && <div className="small">This browser does not support desktop notifications.</div>}
        {perm === 'denied' && <div className="small neg">Blocked by the browser: allow notifications for this site in the browser's site settings, then reload.</div>}
        {perm === 'default' && (
          <button className="btn sm primary" style={{ alignSelf: 'flex-start' }}
            onClick={() => requestNotify().then(p => setPerm(p as NotificationPermission))}>Enable desktop alerts</button>
        )}
      </div>
    </div>
  )
}

function TelegramPanel() {
  const [cfg, setCfg] = useState<TelegramCfg | null>(null)
  const [token, setToken] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [open, setOpen] = useState(true)
  useEffect(() => { live.telegram().then(setCfg).catch(() => {}) }, [])
  const run = async (f: () => Promise<TelegramCfg | unknown>, ok: string) => {
    setMsg(null)
    try { const r = await f(); if (r && typeof r === 'object' && 'configured' in r) setCfg(r as TelegramCfg); setMsg({ ok: true, text: ok }) }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }) }
  }
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Phone alerts (Telegram)</h3>
        <span className={`chip ${cfg?.configured ? 'bull' : ''}`}>{cfg?.configured ? 'connected' : 'not set up'}</span>
      </div>
      <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {!open && <button className="btn sm" onClick={() => setOpen(true)}>{cfg?.configured ? 'Settings' : 'Set up'}</button>}
        {open && (
          <>
            <ol className="small muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6 }}>
              <li>In Telegram, open <b>@BotFather</b>, send <code>/newbot</code>, choose a name. It replies with a <b>token</b>.</li>
              <li>Paste the token below and save.</li>
              <li>Open your new bot in Telegram and press <b>Start</b> (or send it any message).</li>
              <li>Click <b>Detect chat</b>, then <b>Send test</b>.</li>
            </ol>
            <div className="row">
              <input type="text" className="grow" placeholder={cfg?.has_token ? `saved (${cfg.token_hint})` : '123456:ABC-DEF…'} value={token} onChange={e => setToken(e.target.value)} />
              <button className="btn sm" disabled={!token} onClick={() => run(() => live.setTelegram({ token }), 'Token saved.').then(() => setToken(''))}>Save</button>
            </div>
            <div className="row">
              <button className="btn sm" disabled={!cfg?.has_token} onClick={() => run(() => live.detectTelegram(), 'Chat found.')}>Detect chat</button>
              <button className="btn sm" disabled={!cfg?.configured} onClick={() => run(() => live.testTelegram(), 'Test message sent — check your phone.')}>Send test</button>
              {cfg?.chat_id && <span className="small faint num">chat {cfg.chat_id}</span>}
            </div>
            <span className="small faint">The token is stored only on this PC.</span>
          </>
        )}
        {msg && <div className={`small ${msg.ok ? 'pos' : 'neg'}`}>{msg.text}</div>}
      </div>
    </div>
  )
}
