import { useState } from 'react'
import { api, type Agent } from '../api'
import { describeError, signApproval } from '../passkey'
import { Badge, Card, Notice, Page, usePoll, when } from '../ui'

export default function AgentsPage() {
  const q = usePoll(() => api.agents(), [], 5000)
  const [name, setName] = useState('')
  const [cap, setCap] = useState('')
  const [kind, setKind] = useState<'mcp' | 'browser'>('mcp')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [newKey, setNewKey] = useState<{ name: string; key: string } | null>(null)

  const act = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what)
    setError(null)
    try {
      await fn()
      await q.refresh()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(null)
    }
  }

  const create = () => act('create', async () => {
    const terms = { name: name.trim(), spend_cap: cap.trim() || null, kind }
    const webauthn = await signApproval(() => api.agentOptions(terms))
    const made = await api.createAgent(terms, webauthn)
    setNewKey({ name: made.name, key: made.key })
    setName('')
    setCap('')
  })

  const revoke = (a: Agent) => {
    if (!window.confirm(`Revoke ${a.name}? Its key stops working at once and its held carts are closed. This cannot be undone.`)) return
    act(`revoke:${a.agent_id}`, () => api.revokeAgent(a.agent_id))
  }

  const st = q.data
  return (
    <Page title="Agents" kicker="Which agents may shop for you, and the off switch">
      {st && (st.paused
        ? (
          <Card tone="bad" title="All agents are stopped">
            <p>Every agent is refused from its next call, and carts that were waiting for you were closed. Stopped {when(st.paused_at)}.</p>
            <button className="primary" disabled={!!busy}
              onClick={() => act('resume', async () => api.resumeAgents(await signApproval(() => api.resumeOptions())))}>
              {busy === 'resume' ? 'Waiting for your device…' : 'Turn agents back on'}
            </button>
            <p className="muted">Turning them back on gives power back, so it is signed with your passkey.</p>
          </Card>
        )
        : (
          <Card title="Stop all agents">
            <p>One click cuts off every agent. It takes effect on their very next call, and any cart waiting for your approval is closed so it can no longer be paid. No passkey needed: taking power away is never harder than giving it.</p>
            <button className="danger" disabled={!!busy} onClick={() => act('stop', () => api.stopAgents())}>
              {busy === 'stop' ? 'Stopping…' : 'Stop all agents'}
            </button>
          </Card>
        ))}

      {newKey && (
        <Notice tone="warn">
          <strong>Key for {newKey.name}, shown once:</strong> <code className="secret">{newKey.key}</code>
          <br />Give it to that agent as its <code>Authorization: Bearer</code> key for <code>/mcp</code> (for a browser agent: to its checkout guard as <code>GUARD_AGENT_KEY</code>, never to the agent itself). Only a hash is stored, so it cannot be shown again.
          {' '}<button className="link" onClick={() => setNewKey(null)}>I copied it</button>
        </Notice>
      )}
      {error && <Notice tone="bad">{error}</Notice>}

      <Card title="Your agents">
        {st && st.agents.length === 0 && <p className="muted">No agents yet.</p>}
        <ul className="rows">
          {st?.agents.map((a) => (
            <li key={a.agent_id} className="row-link">
              <span className="row-main">
                <span>{a.name} <Badge value={a.status === 'active' ? (st.paused ? 'stopped' : 'active') : a.status} /></span>
                <span className="row-sub">
                  {a.kind === 'browser' ? 'browser agent (key held by its checkout guard)' : 'MCP agent'} · key <code>{a.key_hint}</code> · spent {a.spent_30d} {a.currency} in 30 days
                  {a.spend_cap ? ` of ${a.spend_cap} cap` : ' (no own cap; permission limits still apply)'}
                  {a.user_signed && ' · created with your passkey'}
                </span>
              </span>
              <span className="row-meta">
                {a.status === 'revoked' ? `revoked ${when(a.revoked_at)}` : `last call ${when(a.last_used_at)}`}
                {a.status === 'active' && (
                  <> {' '}<button className="danger" disabled={!!busy} onClick={() => revoke(a)}>Revoke</button></>
                )}
              </span>
            </li>
          ))}
        </ul>
      </Card>

      <Card title="Add an agent">
        <p>Each agent gets its own key, so you can see which one asked for what and cut off just one. A permission you approve belongs to the agent that proposed it; other agents cannot use it. An optional cap limits what the agent can spend over any 30 days, across all its permissions.</p>
        <div className="inline-form">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. Claude desktop" aria-label="Agent name" />
          <select value={kind} onChange={(e) => setKind(e.target.value as 'mcp' | 'browser')} aria-label="Agent kind">
            <option value="mcp">Talks to the gateway (MCP)</option>
            <option value="browser">Shops on websites (checkout guard)</option>
          </select>
          <input value={cap} onChange={(e) => setCap(e.target.value)} placeholder="30-day cap in USD (optional)" inputMode="decimal" aria-label="Spending cap" />
          <button className="primary" disabled={!!busy || !name.trim()} onClick={create}>
            {busy === 'create' ? 'Waiting for your device…' : 'Create agent'}
          </button>
        </div>
        <p className="muted">Giving an agent power to shop is signed with your passkey (when you have one).</p>
      </Card>
    </Page>
  )
}
