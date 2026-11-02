import { useEffect, useState } from 'react'
import { Link } from '../router'
import GatewayProof from '../GatewayProof'
import { Badge, Card, Notice, Page, usePoll, when } from '../ui'
import { desk, type DeskDispute } from './api'

const ACTION_LABEL: Record<string, string> = {
  provide_evidence: 'Contest: send evidence',
  make_offer: 'Offer a refund',
  accept_claim: 'Accept the claim (full refund)',
}

const REC_HEADLINE: Record<string, (d: DeskDispute) => string> = {
  contest: () => 'Contest with evidence',
  refund: (d) => `Refund ${d.amount} ${d.currency}`,
  add_fulfillment: () => 'Add proof of delivery, then contest',
  review: () => 'Review manually',
  wait: () => 'Nothing to send: PayPal is reviewing',
  evidence_not_accepted: (d) => `Evidence not accepted yet: refund ${d.amount} ${d.currency} or escalate`,
}

export default function DisputePage({ id }: { id: string }) {
  const q = usePoll(() => desk.get(id), [id], 8000)
  const d = q.data
  const [note, setNote] = useState('')
  const [action, setAction] = useState('')
  const [offer, setOffer] = useState('')
  const [carrier, setCarrier] = useState('')
  const [tracking, setTracking] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loadedFor, setLoadedFor] = useState<string | null>(null)

  // Prefill the form once per packet version (not on every poll, so edits are kept).
  useEffect(() => {
    if (!d) return
    const key = `${d.dispute_id}:${d.packet.narrative}:${d.packet.available_actions.join()}`
    if (key === loadedFor) return
    setLoadedFor(key)
    setNote(d.packet.narrative)
    const rec = d.packet.recommendation.paypal_call
    setAction(rec && d.packet.available_actions.includes(rec) ? rec : d.packet.available_actions[0] ?? '')
    setOffer(d.amount ?? '')
    setCarrier(d.fulfillment?.carrier ?? '')
    setTracking(d.fulfillment?.tracking_number ?? '')
  }, [d, loadedFor])

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await q.refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!d) return <Page title="Dispute">{q.error ? <Notice tone="bad">{q.error}</Notice> : <p className="muted">Loading…</p>}</Page>
  const p = d.packet
  const f = p.facts
  const rec = p.recommendation
  const auth = p.authorization
  const simulated = d.source === 'simulated'

  return (
    <Page
      title={f.reason_label}
      kicker={<>Case <code>{d.dispute_id}</code> <Badge value={d.status ?? 'unknown'} />
        {simulated && <Badge value="simulated" />}{!d.source_verified && !simulated && d.source === 'webhook' && <Badge value="unverified" />}</>}
      actions={<button disabled={busy} onClick={() => run(() => desk.refresh(id))}>Refresh from PayPal</button>}
    >
      {simulated && <Notice tone="warn"><strong>Simulated case.</strong> Sent by PayPal's webhook simulator; it cannot be answered.</Notice>}

      <Card title="What the buyer and PayPal are saying">
        <dl className="facts">
          <div><dt>Disputed</dt><dd><strong>{f.disputed_amount} {f.currency}</strong> of a {f.payment_amount} {f.currency} payment</dd></div>
          <div><dt>Respond by</dt><dd>{when(f.due_at)}{f.days_left !== null && ` (${f.days_left} days left)`}</dd></div>
          <div><dt>Stage</dt><dd>{f.stage?.toLowerCase()}</dd></div>
          <div><dt>Buyer</dt><dd>{f.buyer}</dd></div>
          <div><dt>Payment</dt><dd><code>{f.capture_id}</code></dd></div>
          <div><dt>PayPal asks for</dt><dd>{p.requested_by_paypal.length ? p.requested_by_paypal.map((x) => x.replaceAll('_', ' ').toLowerCase()).join(', ') : '—'}</dd></div>
        </dl>
        {p.buyer_messages.map((m, i) => <blockquote key={i} className="quote">“{m}”</blockquote>)}
      </Card>

      <Card title="How the buyer's payment gateway authorised this payment" tone={auth ? 'proof-card' : ''}>
        {auth ? (
          <>
            <GatewayProof d={{
              receiptId: auth.receipt_id, userText: auth.user_text, intentTerms: auth.intent_terms,
              intentApprovedAt: auth.intent_approved_at, rules: auth.gateway_checks?.rules,
              semantic: auth.semantic, injectionScore: auth.gateway_checks?.injection_score,
              decision: auth.gateway_checks?.decision, lines: auth.lines, total: auth.total,
              currency: auth.currency, approvedByHuman: auth.approved_by_human, approvedBy: auth.approved_by,
              issuedAt: auth.issued_at, paypalOrderId: auth.paypal_order_id,
              verification: p.checks.map((c) => ({ label: c.name, ok: c.ok })),
              witness: auth.witness ?? null,
              merchant: auth.merchant_signature ?? null,
              pageEvidence: auth.page_evidence ?? null,
              payment: auth.payment_confirmation ?? null,
              ap2: auth.ap2 ?? null,
              intentSignature: auth.intent_user_signed
                ? (p.checks.find((c) => c.name.includes('signed the Intent Mandate'))?.ok ? 'valid' : 'invalid') : 'none',
              cartSignature: auth.cart_user_signed
                ? (p.checks.find((c) => c.name.includes('signed this exact cart'))?.ok ? 'valid' : 'invalid') : 'none',
            }} />
            <p className="muted small">Retrieved by this desk from the buyer's gateway public endpoint, using the receipt id PayPal
              carried on the payment (<Link href={`/receipts/${auth.receipt_id}`}>{auth.receipt_id}</Link>). The signature was checked
              here with the gateway's public key; the desk sees nothing else of the buyer's data.</p>
          </>
        ) : p.checks.length ? (
          <Notice tone="bad">{p.checks[0].name}: {p.checks[0].detail}</Notice>
        ) : (
          <p className="muted">This payment carries no authorization receipt (an ordinary, non-agent purchase). The packet uses the payment record only.</p>
        )}
      </Card>

      <Card title="Suggested response" tone="focus">
        <div className="rec-head">
          <span className="rec-action">{(REC_HEADLINE[rec.action] ?? (() => rec.action))(d)}</span>
          {rec.strength !== 'n/a' && <Badge value={`evidence ${rec.strength}`} />}
        </div>
        <ul className="reasons">{rec.why.map((w, i) => <li key={i}>{w}</li>)}</ul>
        <p className="muted small">
          For scale: an average chargeback costs merchants about ${p.economics.avg_total_cost_usd}, of which about
          ${p.economics.avg_internal_labour_usd} is staff time ({p.economics.source}). This case is for {f.disputed_amount} {f.currency}.
        </p>
      </Card>

      <Card title="Evidence">
        <ul className="checks">
          {p.evidence.map((e) => (
            <li key={e.kind} className={e.have ? 'pass' : 'fail'}>
              <span className="mark" aria-hidden>{e.have ? '✓' : '○'}</span>
              <span className="name">{e.label}</span>
              {e.detail && <span className="detail">{e.detail}</span>}
            </li>
          ))}
        </ul>
        <form className="inline-form" onSubmit={(e) => { e.preventDefault(); run(() => desk.fulfillment(id, carrier, tracking)) }}>
          <input placeholder="Carrier (e.g. UPS)" value={carrier} onChange={(e) => setCarrier(e.target.value)} aria-label="Carrier" />
          <input placeholder="Tracking number" value={tracking} onChange={(e) => setTracking(e.target.value)} aria-label="Tracking number" />
          <button disabled={busy || simulated}>Save delivery proof</button>
        </form>
      </Card>

      <Card title="Response to PayPal">
        {p.available_actions.length === 0 ? (
          <p className="muted">PayPal currently offers no response on this case.</p>
        ) : (
          <>
            <fieldset className="choices">
              <legend className="muted small">Choose what to send</legend>
              {p.available_actions.map((a) => (
                <label key={a} className={`choice ${action === a ? 'on' : ''}`}>
                  <input type="radio" name="action" value={a} checked={action === a} onChange={() => setAction(a)} />
                  {ACTION_LABEL[a] ?? a}{a === rec.paypal_call && <span className="muted small"> · suggested</span>}
                </label>
              ))}
            </fieldset>
            {action === 'provide_evidence' && (
              <p className="muted small">Will be sent as: {(p.evidence_types ?? ['refresh the case first']).map((t) => t.replaceAll('_', ' ').toLowerCase()).join(' + ')}
                {' '}(the evidence types PayPal accepts for “{f.reason_label.toLowerCase()}”), with your note attached.</p>
            )}
            {action === 'make_offer' && (
              <label className="field">Refund amount ({d.currency})
                <input value={offer} onChange={(e) => setOffer(e.target.value)} inputMode="decimal" />
              </label>
            )}
            <label className="field">Note to PayPal (drafted from verified records; edit as needed)
              <textarea rows={7} value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
            </label>
            <label className="confirm">
              <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
              I reviewed this response. Send it to PayPal{simulated ? '' : ' (sandbox)'}.
            </label>
            {error && <Notice tone="bad">{error}</Notice>}
            <div className="actions">
              <button className="primary" disabled={busy || !confirmed || !action || simulated}
                onClick={() => run(async () => { await desk.submit(id, action, note, action === 'make_offer' ? offer : undefined); setConfirmed(false) })}>
                {busy ? 'Sending…' : 'Send to PayPal'}
              </button>
            </div>
          </>
        )}
        {d.actions.length > 0 && (
          <>
            <h3>Sent so far</h3>
            <ul className="checks">
              {d.actions.map((a, i) => (
                <li key={i} className={a.ok ? 'pass' : 'fail'}>
                  <span className="mark">{a.ok ? '✓' : '✕'}</span>
                  <span className="name">{ACTION_LABEL[a.kind] ?? a.kind}</span>
                  <span className="detail">{when(a.at)} by {a.by}{!a.ok && ` · ${JSON.stringify(a.response).slice(0, 160)}`}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
    </Page>
  )
}
