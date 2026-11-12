import { useState, type FormEvent } from 'react'
import { Link, navigate } from '../router'
import { Badge, Card, Notice, Page, usePoll, when } from '../ui'
import { desk } from './api'

export default function DisputesPage() {
  const list = usePoll(() => desk.list(), [], 5000)
  const [caseId, setCaseId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const importCase = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const d = await desk.importCase(caseId)
      navigate(`/seller/disputes/${d.dispute_id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Page title="Disputes" kicker="Cases opened by buyers against your PayPal payments">
      <Card title="Bring in a case">
        <form className="inline-form" onSubmit={importCase}>
          <input placeholder="PayPal case id, e.g. PP-R-ELT-10193319" value={caseId}
            onChange={(e) => setCaseId(e.target.value)} aria-label="PayPal case id" />
          <button className="primary" disabled={busy || !caseId.trim()}>{busy ? 'Fetching…' : 'Fetch from PayPal'}</button>
        </form>
        <p className="muted small">New cases also arrive automatically through PayPal webhooks. Pasting the id is the
          fallback: PayPal's case list can miss sandbox cases.</p>
        {error && <Notice tone="bad">{error}</Notice>}
      </Card>

      <Card title="Open and recent cases">
        {list.error && <Notice tone="bad">{list.error}</Notice>}
        {list.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Case</th><th>Reason</th><th className="num">Disputed</th><th>Due</th><th>Buyer receipt</th><th>Suggested</th></tr></thead>
              <tbody>
                {list.data.map((d) => (
                  <tr key={d.dispute_id} className="clickable" title="Open this case"
                    onClick={() => navigate(`/seller/disputes/${d.dispute_id}`)}>
                    <td>
                      <Link href={`/seller/disputes/${d.dispute_id}`}><code>{d.dispute_id}</code></Link>
                      {d.source === 'simulated' && <div><Badge value="simulated" /></div>}
                    </td>
                    <td>{d.reason_label ?? d.reason}</td>
                    <td className="num">{d.amount} {d.currency}</td>
                    <td>{when(d.due_at)}</td>
                    <td>{d.receipt_id ? <Badge value={d.has_valid_receipt ? 'verified' : 'unverified'} /> : <span className="muted">none</span>}</td>
                    <td>{d.recommendation && <Badge value={d.recommendation} />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          list.data && <div className="empty">No disputes yet. Paste a case id above.</div>
        )}
      </Card>
    </Page>
  )
}
