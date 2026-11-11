// A call to one of PayPal's own agent tools, held by the PayPal guard until the person approves it.
import { useState } from 'react'
import { api } from '../api'
import { Icon } from '../icons'
import { describeError, signApproval } from '../passkey'
import { Card, Hash, Notice, Seal, usePoll, when } from '../ui'

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
      // Your device signs this exact tool + arguments (fingerprint of the arguments below).
      const webauthn = approve ? await signApproval(() => api.paypalActionOptions(id)) : undefined
      await api.decidePaypalAction(id, approve, a.args_hash, webauthn)
      await q.refresh()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="dbody">
      {a.status === 'pending_approval' && (
        <Card title={<>Your decision is needed <span className="meta">until {when(a.approval_expires_at)}</span></>} tone="focus">
          <p style={{ marginTop: 0 }}>An agent wants to use <strong>PayPal's own agent tool</strong> <code>{a.tool}</code>: <strong>{a.summary}</strong>.</p>
          {a.reason && <blockquote className="quote">{a.reason}</blockquote>}
          <p className="muted small">Nothing has been sent to PayPal. If you approve, the PayPal guard runs this call once, with exactly these
            arguments, within 30 minutes; any change needs a new approval.</p>
          {error && <Notice tone="bad">{error}</Notice>}
          <div className="decide-bar">
            <button className="primary" disabled={busy} onClick={() => decide(true)}><Icon name="fingerprint" />Approve this PayPal call</button>
            <button className="danger" disabled={busy} onClick={() => decide(false)}>Reject</button>
            <span className="countdown">signs <Hash value={a.args_hash} n={10} /></span>
          </div>
        </Card>
      )}
      {a.status === 'approved' && <Notice tone="ok">Approved: waiting for the PayPal guard to run it (once).</Notice>}
      {a.status === 'done' && <Notice tone="ok"><strong>Done in PayPal.</strong> The guard ran the approved call once; PayPal's answer is below.</Notice>}
      {a.status === 'failed' && <Notice tone="bad"><strong>PayPal refused the call.</strong> See its answer below.</Notice>}
      {(a.status === 'rejected' || a.status === 'approval_expired') && <Notice tone="bad">Not run: {a.status === 'rejected' ? 'you rejected it' : 'the approval window expired'}. Nothing was sent to PayPal.</Notice>}
      <Card title={<>The exact call <span className="meta">{a.decided_by && a.status !== 'rejected' && <Seal human signed={a.user_signed} />}</span></>}>
        <dl className="facts">
          <div><dt>PayPal tool</dt><dd><code>{a.tool}</code></dd></div>
          <div><dt>Requested</dt><dd>{when(a.created_at)}</dd></div>
          <div><dt>Fingerprint</dt><dd><Hash value={a.args_hash} n={16} /></dd></div>
        </dl>
        <pre className="raw">{JSON.stringify(a.arguments, null, 2)}</pre>
      </Card>
      {a.result?.text && (
        <Card title="PayPal's answer">
          <pre className="raw">{a.result.text}</pre>
        </Card>
      )}
    </div>
  )
}
