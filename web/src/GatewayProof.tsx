import type { Line, Mandate, PageEvidence, PaymentConfirmation } from './api'
import { LineTable, money, pageCharges, when } from './ui'

// The buyer's gateway story, told in four steps: what was asked (Intent Mandate), what the
// gateway checked before paying, what was approved (Cart Mandate), and the payment + proof.
// Used on the public receipt page and on the seller's dispute page.

export type ProofData = {
  receiptId: string
  userText: string
  intentTerms?: Mandate | null
  intentApprovedAt?: string | null
  rules?: { name: string; passed: boolean }[] | null
  semantic?: { match: { matches: number; partial: number; mismatch: number }; model: string; rationale?: string; flagged_skus?: string[] } | null
  injectionScore?: number | null
  decision?: string | null
  lines: Line[]
  total: string
  currency: string
  approvedByHuman: boolean
  approvedBy: string
  issuedAt?: string | null
  paypalOrderId?: string | null
  verification: { label: string; ok: boolean | undefined }[]
  /** Passkey signature by the buyer's own device: valid, invalid, or none (password session). */
  intentSignature?: 'valid' | 'invalid' | 'none'
  cartSignature?: 'valid' | 'invalid' | 'none'
  /** The merchant's signature over the cart's prices and lines. */
  merchant?: { present: boolean; valid: boolean | null; key_id?: string; detail?: string; matches_published_key?: boolean | null } | null
  /** Set when the checkout guard read the cart from a shop's own checkout page. */
  pageEvidence?: PageEvidence | null
  /** Payment at the shop's own checkout, recorded after approval (page carts). */
  payment?: PaymentConfirmation | null
  /** AP2 v0.2 mandates verification. */
  ap2?: { present: boolean; valid: boolean | null; checks: { name: string; ok: boolean }[]; spec?: string; not_implemented?: string[]; has_payment_receipt?: boolean } | null
  /** Outside witness (RFC 3161 timestamp over the audit log). */
  witness?: { anchored: boolean; valid: boolean | null; gen_time?: string; tsa_name?: string; detail?: string; trusted_by?: string } | null
}

function DeviceSig({ state, what }: { state?: 'valid' | 'invalid' | 'none'; what: string }) {
  if (!state) return null
  if (state === 'valid') return <p className="device-sig ok"><span aria-hidden>🔐</span> {what} <strong>signed on the buyer's own device</strong> (passkey). Checked: valid.</p>
  if (state === 'invalid') return <p className="device-sig bad"><span aria-hidden>✕</span> {what}: the device signature does <strong>not</strong> verify.</p>
  return <p className="device-sig none">{what}: approved in a password session, <strong>no device signature</strong>.</p>
}

const pct = (x: number) => `${Math.round(x * 100)}%`

function Step({ n, title, tag, children }: { n: number; title: string; tag?: string; children: React.ReactNode }) {
  return (
    <li className="proof-step">
      <span className="proof-n" aria-hidden>{n}</span>
      <div className="proof-body">
        <h3>{title}{tag && <span className="proof-tag">{tag}</span>}</h3>
        {children}
      </div>
    </li>
  )
}

export default function GatewayProof({ d }: { d: ProofData }) {
  const rules = d.rules ?? []
  const passed = rules.filter((r) => r.passed).length
  const m = d.intentTerms
  return (
    <div className="proof">
      <p className="proof-lede">
        Everything below was <strong>recorded and signed by the buyer's payment gateway before the payment was made</strong>,
        not reconstructed afterwards by the buyer, the agent or the seller.
      </p>
      <ol className="proof-steps">
        <Step n={1} title="What the buyer asked for" tag="Intent Mandate">
          <blockquote className="quote">“{d.userText}”</blockquote>
          {m && (
            <ul className="chips">
              {m.items.map((it, i) => <li key={i}>{it.qty} × {it.description}</li>)}
              <li>budget {money(m.max_total)}</li>
              {m.approval_above && <li>ask above {money(m.approval_above)}</li>}
              <li>only {m.merchants.join(', ')}</li>
              <li>until {when(m.expires_at)}</li>
            </ul>
          )}
          <DeviceSig state={d.intentSignature} what="The Intent Mandate was" />
          <p className="muted small">Approved by the buyer{d.intentApprovedAt ? ` on ${when(d.intentApprovedAt)}` : ''} and sealed by the gateway. The agent could only refer to it, not change it.</p>
        </Step>

        <Step n={2} title="What the gateway checked before paying">
          {rules.length > 0 && (
            <p><strong>{passed}/{rules.length} spending rules passed</strong>{' '}
              <span className="muted small">({rules.map((r) => r.name.replaceAll('_', ' ')).join(', ')})</span></p>
          )}
          {d.semantic ? (
            <div className="match">
              <span className="match-label">Cart matches the request</span>
              <span className="bar"><span className="fill ok" style={{ width: pct(d.semantic.match.matches) }} /></span>
              <strong>{pct(d.semantic.match.matches)}</strong>
              <span className="muted small">{d.semantic.model}</span>
            </div>
          ) : <p className="muted small">Cart-to-request match not recorded on this receipt.</p>}
          {d.injectionScore != null && (
            <p className="muted small">Merchant text screened for instructions aimed at the agent: score {d.injectionScore.toFixed(2)}.</p>
          )}
          {d.decision && <p className="muted small">Gateway decision: {d.decision === 'approve' ? 'approved automatically' : d.decision}.</p>}
        </Step>

        <Step n={3} title="What was approved" tag="Cart Mandate">
          <LineTable lines={d.lines} currency={d.currency} total={d.total} charges={pageCharges(d.pageEvidence)} />
          {d.pageEvidence && (
            <p className="device-sig none"><span aria-hidden>🖥</span> <strong>Cart read from the shop's own checkout page</strong> by
              the checkout guard (<code>{d.pageEvidence.page_url}</code>). This shop does not sign carts, so the buyer had
              to approve it with a passkey whatever the amount.</p>
          )}
          {d.merchant?.present && (d.merchant.valid
            ? <p className="device-sig ok"><span aria-hidden>🏷</span> <strong>Prices and lines signed by the shop</strong> (key <code>{d.merchant.key_id}</code>{d.merchant.matches_published_key ? ', the shop\u2019s published key' : ''}). Checked: valid.</p>
            : <p className="device-sig bad"><span aria-hidden>✕</span> The shop's signature over this cart does <strong>not</strong> verify: {d.merchant.detail}</p>)}
          {d.approvedByHuman && <DeviceSig state={d.cartSignature} what="This exact cart was" />}
          <p className="muted small">
            {d.approvedByHuman
              ? `Approved by ${d.approvedBy} for this exact cart (any change would have needed a new approval).`
              : 'Approved automatically: inside the signed Intent Mandate and below the buyer’s ask-me amount.'}
          </p>
        </Step>

        <Step n={4} title="Payment and proof">
          {d.witness && (d.witness.anchored
            ? (d.witness.valid
              ? <p className="device-sig ok"><span aria-hidden>🕰</span> <strong>Witnessed by an outside timestamp authority</strong>: {d.witness.tsa_name}{d.witness.gen_time && ` at ${when(d.witness.gen_time)}`}. This record existed then and cannot be rewritten afterwards without it showing, even by the gateway's operator.</p>
              : <p className="device-sig bad"><span aria-hidden>✕</span> <strong>The outside witness does not match</strong>: {d.witness.detail}</p>)
            : <p className="device-sig none">Not yet witnessed by an outside timestamp authority ({d.witness.detail}).</p>)}
          {d.ap2?.present && (
            <details className={`device-sig ${d.ap2.valid ? 'ok' : 'bad'} ap2`}>
              <summary><span aria-hidden>🧾</span> <strong>AP2 v0.2 mandates</strong> (Checkout + Payment{d.ap2.has_payment_receipt ? ' + Payment Receipt' : ''}):
                {' '}{d.ap2.checks.filter((c) => c.ok).length}/{d.ap2.checks.length} dispute checks pass</summary>
              <ul className="checks">
                {d.ap2.checks.map((c) => (
                  <li key={c.name} className={c.ok ? 'pass' : 'fail'}><span className="mark">{c.ok ? '✓' : '✕'}</span><span className="name">{c.name}</span></li>
                ))}
              </ul>
              {d.ap2.not_implemented && <p className="muted small">Not implemented yet: {d.ap2.not_implemented.join('; ')}. {d.ap2.spec}</p>}
            </details>
          )}
          {d.payment && (
            <p className={`device-sig ${d.payment.signature_valid === false ? 'bad' : d.payment.checked_with_paypal ? 'ok' : 'none'}`}>
              <span aria-hidden>💳</span> <strong>{d.payment.checked_with_paypal ? 'Payment checked with PayPal' : 'Payment reported by the checkout guard'}</strong>:
              order <code>{d.payment.paypal_order_id}</code>
              {d.payment.checked_with_paypal && <> ({d.payment.status}, {d.payment.amount} {d.payment.currency})</>}
              , {when(d.payment.confirmed_at)}. {d.payment.signature_valid === false ? 'Signature does not verify.' : 'Signed by the gateway.'}
            </p>
          )}
          {d.pageEvidence && !d.paypalOrderId && !d.payment && (
            <p className="muted small">Paid at the shop's own checkout (PayPal), opened by the gateway's signed checkout permit for this exact cart.</p>
          )}
          <p>PayPal order <code>{d.paypalOrderId || '—'}</code> · receipt <code>{d.receiptId}</code>{d.issuedAt && <> · {when(d.issuedAt)}</>}</p>
          <ul className="checks">
            {d.verification.map((v) => (
              <li key={v.label} className={v.ok ? 'pass' : 'fail'}>
                <span className="mark" aria-hidden>{v.ok ? '✓' : '✕'}</span>
                <span className="name">{v.label}</span>
              </li>
            ))}
          </ul>
        </Step>
      </ol>
    </div>
  )
}
