import { api } from '../api'
import { Link } from '../router'
import { Badge, Card, Page, usePoll, when } from '../ui'

export default function Inbox() {
  const intents = usePoll(() => api.intents(), [])
  const purchases = usePoll(() => api.purchases(), [])

  const pendingIntents = (intents.data ?? []).filter((i) => i.status === 'pending_user')
  const pendingCarts = (purchases.data ?? []).filter((p) => p.status === 'pending_approval')
  const nothing = intents.data && purchases.data && !pendingIntents.length && !pendingCarts.length

  return (
    <Page title="Inbox" kicker="What your agent is waiting on">
      {(intents.error || purchases.error) && <div className="notice bad">{intents.error ?? purchases.error}</div>}

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
              <thead><tr><th>When</th><th>Merchant</th><th className="num">Total</th><th>Decision</th><th>Status</th></tr></thead>
              <tbody>
                {purchases.data.slice(0, 15).map((p) => (
                  <tr key={p.purchase_id}>
                    <td><Link href={`/purchases/${p.purchase_id}`}>{when(p.created_at)}</Link></td>
                    <td>{p.merchant_id}</td>
                    <td className="num">{p.total} {p.currency}</td>
                    <td><Badge value={p.decision} /></td>
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
