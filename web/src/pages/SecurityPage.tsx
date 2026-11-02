import { useState } from 'react'
import { browserSupportsWebAuthn } from '@simplewebauthn/browser'
import { api, setAuth, type SessionInfo } from '../api'
import { describeError, enrollPasskey } from '../passkey'
import { Card, Notice, Page, usePoll, when } from '../ui'

function device(ua: string | null) {
  if (!ua) return 'Script or unknown device'
  const os = /iPhone|iPad/.test(ua) ? 'iPhone/iPad' : /Android/.test(ua) ? 'Android' : /Mac OS X/.test(ua) ? 'Mac'
    : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : ''
  const app = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari'
    : /Firefox\//.test(ua) ? 'Firefox' : /python|httpx|curl/i.test(ua) ? 'script' : 'browser'
  return [app, os].filter(Boolean).join(' on ')
}

function Sessions() {
  const q = usePoll(() => api.sessions(), [], 30000)
  const [busy, setBusy] = useState(false)
  const act = async (f: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await f()
      await q.refresh()
    } finally {
      setBusy(false)
    }
  }
  const others = (q.data ?? []).filter((s) => !s.current)
  return (
    <Card title="Where you are signed in">
      <p className="muted small">
        Sessions end after 2 hours without activity and after 12 hours at most. If you see one you do not
        recognise, end it, then change your password.
      </p>
      <ul className="rows">
        {(q.data ?? []).map((s: SessionInfo) => (
          <li key={s.id} className="row-link">
            <span className="row-main">
              {device(s.user_agent)} {s.current && <span className="badge ok">this browser</span>}
              <span className="row-sub">signed in with {s.method} {when(s.created_at)}{s.ip ? ` · ${s.ip}` : ''} · active {when(s.last_seen_at)}</span>
            </span>
            <span className="row-meta">
              {s.current
                ? <button className="link" disabled={busy} onClick={() => act(async () => { await api.logout(); setAuth('out') })}>Sign out</button>
                : <button className="link danger" disabled={busy} onClick={() => act(() => api.endSession(s.id))}>End</button>}
            </span>
          </li>
        ))}
      </ul>
      {others.length > 0 && (
        <div className="actions">
          <button className="danger" disabled={busy} onClick={() => act(() => api.endOtherSessions())}>
            Sign out everywhere else ({others.length})
          </button>
        </div>
      )}
    </Card>
  )
}

export default function SecurityPage() {
  const q = usePoll(() => api.passkeys(), [], 10000)
  const [label, setLabel] = useState('This Mac (Touch ID)')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const supported = browserSupportsWebAuthn()

  const enroll = async () => {
    setBusy(true)
    setError(null)
    try {
      await enrollPasskey(label)
      await q.refresh()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  const st = q.data
  return (
    <Page title="Security" kicker="Who can approve what your agent buys">
      <Card title="Passkey approvals">
        <p>
          With a passkey, every permission and every held cart you approve is <strong>signed by your own device</strong>{' '}
          (Touch ID, Face ID or a security key). The signature covers exactly what you saw, goes into the receipt,
          and anyone can check it later. The gateway never holds the key, so it cannot approve anything in your name.
        </p>
        {st && (st.required
          ? <Notice tone="ok"><strong>On.</strong> Approvals require your passkey; a password alone cannot approve.</Notice>
          : <Notice tone="warn"><strong>Off.</strong> Approvals currently rely on your password session only. Add a passkey to turn signing on.</Notice>)}
        {!supported && <Notice tone="bad">This browser does not support passkeys.</Notice>}
        <div className="inline-form">
          <input value={label} onChange={(e) => setLabel(e.target.value)} aria-label="Passkey name" />
          <button className="primary" disabled={busy || !supported} onClick={enroll}>{busy ? 'Waiting for your device…' : 'Add a passkey'}</button>
        </div>
        {error && <Notice tone="bad">{error}</Notice>}
      </Card>
      {st && st.passkeys.length > 0 && (
        <Card title="Your passkeys">
          <ul className="rows">
            {st.passkeys.map((p) => (
              <li key={p.credential_id} className="row-link">
                <span className="row-main">{p.label ?? 'passkey'} <span className="row-sub"><code>{p.credential_id}</code></span></span>
                <span className="row-meta">added {when(p.created_at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Sessions />
    </Page>
  )
}
