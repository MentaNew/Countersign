// Judge mode: drive a scripted demo agent from the Inbox. The person still approves everything themselves.
import { useEffect, useState } from 'react'
import { api, ApiError, type DemoState } from '../api'
import { Icon } from '../icons'
import { Notice } from '../ui'

export default function DemoPanel({ onOpen }: { onOpen: (id: string) => void }) {
  const [state, setState] = useState<DemoState | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = () => api.demoState().then(setState).catch((e) => {
    if (!(e instanceof ApiError && e.status === 404)) setError(String(e.message ?? e))
  })
  useEffect(() => {
    load()
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [])
  if (!state) return null

  const current = state.permissions[0]
  const active = state.permissions.find((p) => p.status === 'active')
  const pending = current?.status === 'pending_user' ? current : null
  const act = async (key: string, f: () => Promise<{ intent_id?: string; purchase_id?: string }>) => {
    setBusy(key)
    setError(null)
    try {
      const r = await f()
      onOpen((r.purchase_id ?? r.intent_id)!)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }
  const offline = state.verifier.startsWith('Stub')

  return (
    <section className="card demo-panel" aria-label="Try it">
      <h2><Icon name="agent" /> Try it: a demo agent shops for you <span className="meta">judge mode</span></h2>
      <p className="muted small" style={{ marginTop: 0 }}>
        You are the person. The demo agent can only do what any agent can: ask for a permission, then ask to buy.
        The gateway decides each cart; doubtful ones wait for you in this Inbox. Approved carts create real PayPal
        sandbox orders.
      </p>
      <ol className="demo-steps">
        <li className={active ? 'done' : ''}>
          <span className="n">1</span>
          <div>
            <div className="what">The agent asks for a permission</div>
            <div className="muted small">“{state.request}”</div>
            <div className="actions">
              {pending
                ? <button className="primary sm" onClick={() => onOpen(pending.intent_id)}><Icon name="permission" />Review and approve it</button>
                : <button className={active ? 'sm' : 'primary sm'} disabled={!!busy} onClick={() => act('propose', () => api.demoPropose())}>
                    <Icon name="agent" />{active ? 'Ask for a new permission' : 'Ask the demo agent'}</button>}
            </div>
          </div>
        </li>
        <li className={active ? '' : 'locked'}>
          <span className="n">2</span>
          <div>
            <div className="what">Then it tries carts {!active && <span className="muted small">· approve the permission first</span>}</div>
            <div className="demo-scenarios">
              {state.scenarios.map((s) => (
                <button key={s.id} className="sm" disabled={!active || !!busy} title={`Expected: ${s.expect}`}
                  onClick={() => active && act(s.id, () => api.demoBuy(active.intent_id, s.id))}>
                  {busy === s.id ? 'Asking…' : s.label}
                </button>
              ))}
            </div>
            <div className="muted small">Hover a button for what should happen. {offline && 'This server uses the offline checker, so the AI-only case (US Letter instead of A4) is not caught here.'}</div>
          </div>
        </li>
      </ol>
      {error && <Notice tone="bad">{error}</Notice>}
    </section>
  )
}
