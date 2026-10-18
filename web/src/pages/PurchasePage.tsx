import { useEffect, useState } from 'react'
import { api } from '../api'
import { Link } from '../router'
import { Badge, Card, Hash, LineTable, Notice, Page, VerdictPanel, usePoll, when } from '../ui'

function useCountdown(iso: string | null) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  if (!iso) return null
  const s = Math.max(0, Math.floor((new Date(iso).getTime() - now) / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export default function PurchasePage({ id }: { id: string }) {
  const q = usePoll(() => api.purchase(id), [id])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const left = useCountdown(q.data?.status === 'pending_approval' ? q.data.approval_expires_at : null)

  const decide = async (approve: boolean) => {
    if (!q.data) return
    setBusy(true)
    setError(null)
    try {
      // The cart hash shown on this screen binds the approval to exactly this cart.
      await api.decidePurchase(id, approve, q.data.cart_hash)
      await q.refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const p = q.data
  if (!p) return <Page title="Cart">{q.error ? <Notice tone="bad">{q.error}</Notice> : <p className="muted">Loading…</p>}</Page>

  return (
    <Page
      title={`Cart at ${p.merchant_id}`}
      kicker={<>Purchase <code>{p.purchase_id}</code> {p.status !== 'blocked' && <Badge value={p.decision} />} <Badge value={p.status} /></>}
      actions={<Link href={`/intents/${p.intent_id}`}>Permission →</Link>}
    >
      {p.status === 'pending_approval' && (
        <Card title="Your decision is needed" tone="focus">
          <ul className="reasons">{p.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          <p className="muted small">
            Check every line below. Approving signs this exact cart (hash <Hash value={p.cart_hash} />); any change
            to it would need a new approval. Expires in {left}.
          </p>
          {error && <Notice tone="bad">{error}</Notice>}
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => decide(true)}>Approve this cart</button>
            <button className="danger" disabled={busy} onClick={() => decide(false)}>Reject</button>
          </div>
        </Card>
      )}
      {p.status === 'blocked' && (
        <Notice tone="bad"><strong>Blocked by the gateway.</strong> {p.error ?? p.reasons.join('; ')}</Notice>
      )}
      {p.status === 'failed' && <Notice tone="bad"><strong>Order failed at PayPal.</strong> {p.error}</Notice>}
      {(p.status === 'ordered' || p.status === 'captured') && (
        <Notice tone="ok">
          {p.approved_by ? `Approved by ${p.approved_by}. ` : 'Approved automatically by policy. '}
          PayPal order <code>{p.paypal_order_id}</code> ({p.paypal_status}).
          {p.receipt_id && <> Receipt <Link href={`/receipts/${p.receipt_id}`}>{p.receipt_id}</Link>.</>}
        </Notice>
      )}
      {p.status === 'ordered' && p.payer_action_url && (
        <Card title="Pay with PayPal">
          <p>The gateway created the order. Complete payment as the buyer (sandbox personal account).
            You will come back here when it is captured.</p>
          <a className="button primary" href={p.payer_action_url}>Pay with PayPal (sandbox)</a>
        </Card>
      )}

      <Card title="Every line in the cart">
        <LineTable lines={p.lines} currency={p.currency} total={p.total} />
        <p className="muted small">Titles and prices come from the merchant catalog, looked up by the gateway, not from the agent.</p>
      </Card>

      <Card title="Why the gateway decided this">
        <VerdictPanel v={p.verdict} texts={p.context_texts} />
      </Card>

      <Card title="Record">
        <dl className="facts">
          <div><dt>Requested</dt><dd>{when(p.created_at)}</dd></div>
          <div><dt>Cart hash</dt><dd><Hash value={p.cart_hash} n={24} /></dd></div>
          <div><dt>Receipt</dt><dd>{p.receipt_id ? <Link href={`/receipts/${p.receipt_id}`}>{p.receipt_id}</Link> : '—'}</dd></div>
          <div><dt>PayPal order</dt><dd>{p.paypal_order_id ?? '—'}</dd></div>
        </dl>
        <p><Link href={`/log?purchase_id=${p.purchase_id}`}>Audit log for this cart →</Link></p>
      </Card>
    </Page>
  )
}
