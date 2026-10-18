import { api, type Line } from '../api'
import { Card, LineTable, Notice, Page, usePoll, when } from '../ui'

function Check({ ok, label }: { ok: boolean | undefined; label: string }) {
  return <li className={ok ? 'pass' : 'fail'}><span className="mark">{ok ? '✓' : '✕'}</span><span className="name">{label}</span></li>
}

// Public page: anyone with the receipt id (a seller, a dispute analyst) can verify it.
export default function ReceiptPage({ id }: { id: string }) {
  const q = usePoll(() => api.verifyReceipt(id), [id], 5000)
  const v = q.data
  if (!v) return <Page title="Authorization receipt">{q.error ? <Notice tone="bad">{q.error}</Notice> : <p className="muted">Verifying…</p>}</Page>
  const r = v.receipt ?? {}

  return (
    <Page title="Authorization receipt" kicker={<code>{id}</code>}>
      {v.valid
        ? <Notice tone="ok"><strong>Valid.</strong> Signed by the gateway and anchored in an intact audit log.</Notice>
        : <Notice tone="bad"><strong>Not valid.</strong> {v.reason ?? 'One of the checks below failed.'}</Notice>}
      <Card title="Checks">
        <ul className="checks">
          <Check ok={v.signature_valid} label="Ed25519 signature over the receipt" />
          <Check ok={v.log_anchor_valid} label={`Log entry #${String(r.log_seq ?? '?')} matches the hash in the receipt`} />
          <Check ok={v.chain_valid} label={`Whole log chain re-hashes (${v.chain_entries ?? 0} entries)`} />
        </ul>
      </Card>
      {v.receipt && (
        <>
          <Card title="What was authorised">
            <dl className="facts">
              <div><dt>User asked</dt><dd>{String(r.user_text ?? '')}</dd></div>
              <div><dt>Merchant</dt><dd>{String(r.merchant_id ?? '')}</dd></div>
              <div><dt>Approved</dt><dd>{r.approved_by_human ? `by ${String(r.approved_by)} (human, cart-bound token)` : 'by policy, within the signed permission'}</dd></div>
              <div><dt>PayPal order</dt><dd>{String(r.paypal_order_id ?? '')}</dd></div>
              <div><dt>Issued</dt><dd>{when(r.issued_at as string)}</dd></div>
              <div><dt>Signing key</dt><dd><code>{v.key_id}</code></dd></div>
            </dl>
            <LineTable lines={(r.lines ?? []) as Line[]} currency={String(r.currency ?? '')} total={String(r.total ?? '0')} />
          </Card>
          <Card title="Raw receipt">
            <pre className="raw">{JSON.stringify(r, null, 2)}</pre>
          </Card>
        </>
      )}
    </Page>
  )
}
