import { api, type Line, type Mandate, type PageEvidence } from '../api'
import GatewayProof, { type ProofData } from '../GatewayProof'
import { Card, Notice, Page, usePoll } from '../ui'

const sigState = (x?: { valid: boolean }) => (x ? (x.valid ? 'valid' : 'invalid') : 'none') as 'valid' | 'invalid' | 'none'

// Public page: anyone with the receipt id (a seller, a dispute analyst) can verify it.
export default function ReceiptPage({ id }: { id: string }) {
  const q = usePoll(() => api.verifyReceipt(id), [id], 5000)
  const v = q.data
  if (!v) return <Page title="Authorization receipt">{q.error ? <Notice tone="bad">{q.error}</Notice> : <p className="muted">Verifying…</p>}</Page>
  const r = v.receipt ?? {}

  return (
    <Page title="Authorization receipt" kicker={<code>{id}</code>}>
      {v.valid
        ? <Notice tone="ok"><strong>Valid.</strong> Signed by the buyer's payment gateway and anchored in its intact audit log. The four steps below are what it recorded before paying.</Notice>
        : <Notice tone="bad"><strong>Not valid.</strong> {v.reason ?? 'One of the checks below failed.'}</Notice>}
      {v.receipt && (
        <>
          <Card title="How the buyer's payment gateway authorised this payment">
            <GatewayProof d={{
              receiptId: id,
              userText: String(r.user_text ?? ''),
              intentTerms: (r.intent_mandate as { terms?: Mandate } | undefined)?.terms ?? null,
              intentApprovedAt: (r.intent_mandate as { approved_at?: string } | undefined)?.approved_at ?? null,
              rules: (r.gateway_checks as { rules?: { name: string; passed: boolean }[] } | undefined)?.rules ?? null,
              semantic: ((r.verdict_summary as { semantic?: ProofData['semantic'] } | undefined)?.semantic) ?? null,
              injectionScore: (r.verdict_summary as { injection_score?: number | null } | undefined)?.injection_score ?? null,
              decision: String(r.decision ?? ''),
              lines: (r.lines ?? []) as Line[], total: String(r.total ?? '0'), currency: String(r.currency ?? ''),
              approvedByHuman: Boolean(r.approved_by_human), approvedBy: String(r.approved_by ?? ''),
              issuedAt: r.issued_at as string, paypalOrderId: String(r.paypal_order_id ?? ''),
              intentSignature: sigState(v.user_signatures?.intent_mandate),
              cartSignature: sigState(v.user_signatures?.cart_mandate),
              witness: v.witness ?? null,
              merchant: v.merchant_signature ?? null,
              payment: v.payment_confirmation ?? null,
              pageEvidence: (r.cart_mandate as { page_evidence?: PageEvidence } | undefined)?.page_evidence ?? null,
              ap2: v.ap2 ?? null,
              verification: [
                { label: 'Signed by the gateway (Ed25519)', ok: v.signature_valid },
                { label: `Anchored in audit log entry #${String(r.log_seq ?? '?')}`, ok: v.log_anchor_valid },
                { label: `Audit log intact (${v.chain_entries ?? 0} entries re-hashed)`, ok: v.chain_valid },
              ],
            }} />
          </Card>
          <Card title="Raw receipt">
            <pre className="raw">{JSON.stringify(r, null, 2)}</pre>
          </Card>
        </>
      )}
    </Page>
  )
}
