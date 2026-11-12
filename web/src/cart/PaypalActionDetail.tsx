// A call to one of PayPal's own agent tools, held by the PayPal guard until the person approves it.
import { useState } from 'react'
import { api } from '../api'
import { Icon } from '../icons'
import { describeError, signApproval } from '../passkey'
import { Card, Hash, Notice, Seal, usePoll, when } from '../ui'

/** The few fields of PayPal's answer a person cares about (id, name, status, amount). */
function highlights(text: string | undefined): [string, string][] {
  if (!text) return []
  let j: Record<string, unknown>
  try {
    j = JSON.parse(text)
  } catch {
    return []
  }
  const out: [string, string][] = []
  const label: Record<string, string> = {
    id: 'PayPal ID', name: 'Name', type: 'Type', status: 'Status', create_time: 'Created',
    category: 'Category', description: 'Description',
  }
  for (const [k, l] of Object.entries(label)) {
    const v = j[k]
    if (typeof v === 'string' && v) out.push([l, k === 'create_time' ? when(v) : v])
  }
  const amt = j.amount as { value?: string; currency_code?: string } | undefined
  if (amt?.value) out.push(['Amount', `${amt.value} ${amt.currency_code ?? ''}`])
  return out
}

const STEP: Record<string, { label: string; sub: string; icon: string }> = {
  paypal_action_requested: { label: 'Your agent asked', sub: 'held by the PayPal guard: nothing sent to PayPal', icon: 'agent' },
  paypal_action_approved: { label: 'You said yes', sub: 'for these exact details only', icon: 'fingerprint' },
  paypal_action_rejected: { label: 'You said no', sub: 'nothing sent to PayPal', icon: 'x' },
  paypal_action_executing: { label: 'Sent to PayPal, once', sub: 'the approval is now used up', icon: 'lock' },
  paypal_action_done: { label: 'PayPal confirmed it', sub: 'its answer is saved with the request', icon: 'check' },
  paypal_action_failed: { label: 'PayPal refused it', sub: 'nothing changed', icon: 'x' },
}

export default function PaypalActionDetail({ id }: { id: string }) {
  const q = usePoll(() => api.paypalAction(id), [id], 3000)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const a = q.data
  if (!a) return <div className="dbody">{q.error ? <Notice tone="bad">{q.error}</Notice> : <p className="muted">Loading…</p>}</div>
  const decide = async (approve: boolean) => {
    setBusy(true)
    setError(null)
    try {
      // Your device signs this exact tool + arguments (the fingerprint in the details).
      const webauthn = approve ? await signApproval(() => api.paypalActionOptions(id)) : undefined
      await api.decidePaypalAction(id, approve, a.args_hash, webauthn)
      await q.refresh()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }
  const time = a.approval_expires_at ? new Date(a.approval_expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : null
  const facts = highlights(a.result?.text)
  return (
    <div className="dbody">
      {a.status === 'pending_approval' && (
        <Card title={<>Your agent wants to do this in your PayPal account {time && <span className="meta">answer by {time}</span>}</>} tone="focus">
          <p style={{ marginTop: 0, fontSize: 17 }}><strong>{a.summary}</strong></p>
          <p style={{ margin: '0 0 12px' }}>
            {a.impact === 'money'
              ? <span className="pill badge warn">Money moves if you say yes</span>
              : <span className="pill badge">No money moves: it changes your PayPal account</span>}
          </p>
          {a.reason && <blockquote className="quote">The agent's reason: “{a.reason}”</blockquote>}
          <p className="muted small">Nothing has happened yet. If you say yes, it happens <strong>once</strong>, exactly as shown
            in the details below. If the agent changes anything, it has to ask you again.{time && <> If you don't answer by {time}, nothing happens.</>}</p>
          {error && <Notice tone="bad">{error}</Notice>}
          <div className="decide-bar">
            <button className="primary" disabled={busy} onClick={() => decide(true)}><Icon name="fingerprint" />Yes, do it</button>
            <button className="danger" disabled={busy} onClick={() => decide(false)}>No</button>
          </div>
        </Card>
      )}
      {a.status === 'approved' && <Notice tone="ok">You said yes. Waiting for the agent to do it (only once).</Notice>}
      {a.status === 'executing' && <Notice tone="warn">Doing it in PayPal now…</Notice>}
      {a.status === 'done' && (
        <div className="ppa-done">
          <div className="ppa-done-head">
            <span className="ppa-check"><Icon name="check" /></span>
            <div>
              <div className="ppa-done-title">Done in your PayPal account</div>
              <div>{a.summary}</div>
            </div>
          </div>
          {facts.length > 0 && (
            <dl className="facts ppa-facts">
              {facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{k === 'PayPal ID' ? <code>{v}</code> : v}</dd></div>)}
            </dl>
          )}
          <p className="small" style={{ margin: '10px 0 0' }}>It ran <strong>once</strong>, exactly as you approved. If the agent wants
            to do it again, or anything slightly different, it has to ask you again.</p>
        </div>
      )}
      {a.status === 'failed' && <Notice tone="bad"><strong>PayPal said no.</strong> Nothing changed. PayPal's answer is in the details.</Notice>}
      {a.status === 'rejected' && <Notice tone="bad">You said no. Nothing happened in PayPal.</Notice>}
      {a.status === 'approval_expired' && <Notice tone="bad">You didn't answer in time, so nothing happened. The agent has to ask again.</Notice>}
      {(a.history?.length ?? 0) > 1 && (
        <Card title={<>What happened <span className="meta">every step is in the tamper-evident audit log</span></>}>
          <ul className="track">
            {a.history!.map((h) => {
              const st = STEP[h.event] ?? { label: h.event, sub: '', icon: 'log' }
              const good = !['paypal_action_rejected', 'paypal_action_failed'].includes(h.event)
              return (
                <li key={h.seq} className={good ? 'done' : ''}>
                  <span className="dot"><Icon name={st.icon} /></span>
                  <span><div className="what">{st.label}{h.event === 'paypal_action_approved' && <> <Seal human signed={a.user_signed} short /></>}</div>
                    <div className="sub">{st.sub}</div></span>
                  <span className="at" title={h.at}>#{h.seq} · {new Date(h.at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
                </li>
              )
            })}
          </ul>
        </Card>
      )}
      {a.status !== 'pending_approval' && a.status !== 'done' && (
        <Card title="What was asked">
          <p style={{ margin: 0 }}><strong>{a.summary}</strong></p>
          {a.reason && <p className="muted small">The agent's reason: “{a.reason}”</p>}
        </Card>
      )}
      <Card>
        <details>
          <summary className="link">Technical details (exact request sent to PayPal)</summary>
          <dl className="facts">
            <div><dt>PayPal agent tool</dt><dd><code>{a.tool}</code></dd></div>
            <div><dt>Asked at</dt><dd>{when(a.created_at)}</dd></div>
            <div><dt>Fingerprint you approve</dt><dd><Hash value={a.args_hash} n={16} /></dd></div>
          </dl>
          <pre className="raw">{JSON.stringify(a.arguments, null, 2)}</pre>
          {a.result?.text && <><p className="muted small">PayPal's answer:</p><pre className="raw">{a.result.text}</pre></>}
        </details>
      </Card>
    </div>
  )
}
