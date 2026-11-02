import { useState } from 'react'
import { api } from '../api'
import { Link } from '../router'
import { Badge, Card, Page, usePoll, when } from '../ui'
import { enableNotifications, notificationPermission } from '../waiting'

/** What to set up before letting an agent spend real money. Disappears when everything is done. */
function SetupChecklist() {
  const keys = usePoll(() => api.passkeys(), [], 30000)
  const agents = usePoll(() => api.agents(), [], 30000)
  const [perm, setPerm] = useState(notificationPermission())
  if (!keys.data || !agents.data) return null
  const list = agents.data.agents
  const steps = [
    {
      done: keys.data.required,
      title: 'Add a passkey',
      why: 'Only your fingerprint, face or security key can then approve what an agent buys.',
      action: <Link href="/security">Add one →</Link>,
    },
    {
      done: list.some((a) => a.created_by !== 'config' && a.status === 'active' && a.spend_cap),
      title: 'Give each agent its own key and a spending cap',
      why: 'Each agent then has a 30-day limit and its own off switch.',
      action: <Link href="/agents">Create an agent →</Link>,
    },
    {
      done: !list.some((a) => a.created_by === 'config' && a.status === 'active'),
      title: 'Switch off the shared default key',
      why: 'The key from the server settings has no cap and is shared by every script that knows it.',
      action: <Link href="/agents">Revoke "default" →</Link>,
    },
    {
      done: perm === 'granted' || perm === 'unsupported',
      title: 'Get notified when an agent needs you',
      why: 'A desktop notification on this device while the app is open in a tab.',
      action: perm === 'denied'
        ? <span className="muted small">Blocked in this browser's site settings.</span>
        : <button className="link" onClick={async () => setPerm(await enableNotifications())}>Turn on →</button>,
    },
  ]
  const left = steps.filter((s) => !s.done).length
  if (!left) return null
  return (
    <Card title={`Set up for real use · ${steps.length - left} of ${steps.length} done`}>
      <ul className="checklist">
        {steps.map((s) => (
          <li key={s.title} className={s.done ? 'done' : ''}>
            <span className="check-dot" aria-hidden>{s.done ? '✓' : ''}</span>
            <span className="row-main">{s.title}<span className="row-sub">{s.why}</span></span>
            {!s.done && <span className="row-meta">{s.action}</span>}
          </li>
        ))}
      </ul>
    </Card>
  )
}

export default function Inbox() {
  const intents = usePoll(() => api.intents(), [])
  const purchases = usePoll(() => api.purchases(), [])

  const pendingIntents = (intents.data ?? []).filter((i) => i.status === 'pending_user')
  const pendingCarts = (purchases.data ?? []).filter((p) => p.status === 'pending_approval')
  const nothing = intents.data && purchases.data && !pendingIntents.length && !pendingCarts.length

  return (
    <Page title="Inbox" kicker="What your agent is waiting on">
      {(intents.error || purchases.error) && <div className="notice bad">{intents.error ?? purchases.error}</div>}

      <SetupChecklist />

      {nothing && <div className="empty">Nothing needs your decision right now.</div>}

      {pendingIntents.length > 0 && (
        <Card title="Purchase permissions to review">
          <ul className="rows">
            {pendingIntents.map((i) => (
              <li key={i.intent_id}>
                <Link href={`/intents/${i.intent_id}`} className="row-link">
                  <span className="row-main">{i.user_text}</span>
                  <span className="row-meta">{when(i.created_at)} <Badge value={i.status} /></span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {pendingCarts.length > 0 && (
        <Card title="Carts held for your approval">
          <ul className="rows">
            {pendingCarts.map((p) => (
              <li key={p.purchase_id}>
                <Link href={`/purchases/${p.purchase_id}`} className="row-link">
                  <span className="row-main">
                    {p.lines.length} line{p.lines.length > 1 ? 's' : ''} at {p.merchant_id} · {p.total} {p.currency}
                    <span className="row-sub">{p.reasons[0]}</span>
                  </span>
                  <span className="row-meta">{when(p.created_at)} <Badge value={p.status} /></span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="Recent activity">
        {purchases.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>When</th><th>Merchant</th><th className="num">Total</th><th className="hide-sm">Decision</th><th>Status</th></tr></thead>
              <tbody>
                {purchases.data.slice(0, 15).map((p) => (
                  <tr key={p.purchase_id}>
                    <td><Link href={`/purchases/${p.purchase_id}`}>{when(p.created_at)}</Link></td>
                    <td>{p.merchant_id}</td>
                    <td className="num">{p.total} {p.currency}</td>
                    <td className="hide-sm"><Badge value={p.decision} /></td>
                    <td><Badge value={p.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">No purchase requests yet.</p>
        )}
      </Card>

      <Card title="Permissions">
        <ul className="rows">
          {(intents.data ?? []).map((i) => (
            <li key={i.intent_id}>
              <Link href={`/intents/${i.intent_id}`} className="row-link">
                <span className="row-main">{i.user_text}</span>
                <span className="row-meta"><Badge value={i.status} /></span>
              </Link>
            </li>
          ))}
        </ul>
      </Card>
    </Page>
  )
}
