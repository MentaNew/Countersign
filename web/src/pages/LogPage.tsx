import { Fragment, useState } from 'react'
import { api } from '../api'
import { Link } from '../router'
import { Card, Hash, Notice, Page, usePoll, when } from '../ui'

export default function LogPage({ query }: { query: URLSearchParams }) {
  const intentId = query.get('intent_id') ?? undefined
  const purchaseId = query.get('purchase_id') ?? undefined
  const q = { ...(intentId && { intent_id: intentId }), ...(purchaseId && { purchase_id: purchaseId }) }
  const log = usePoll(() => api.log(q), [intentId, purchaseId])
  const chain = usePoll(() => api.verifyLog(), [], 4000)
  const [open, setOpen] = useState<number | null>(null)
  const anchors = usePoll(() => api.anchors(), [], 15000)
  const [anchoring, setAnchoring] = useState(false)

  return (
    <Page
      title="Audit log"
      kicker={intentId ? <>Permission <code>{intentId}</code></> : purchaseId ? <>Cart <code>{purchaseId}</code></> : 'Every event, hash-chained'}
      actions={(intentId || purchaseId) && <Link href="/log">Show all</Link>}
    >
      {chain.data && (
        chain.data.valid
          ? <Notice tone="ok"><strong>Chain intact.</strong> All {chain.data.entries} entries re-hash to the stored values; nothing was edited or removed.</Notice>
          : <Notice tone="bad"><strong>Chain broken at entry #{chain.data.first_bad_seq}.</strong> A record was changed after it was written; receipts that depend on it no longer verify.</Notice>
      )}
      <Card title="Outside witnesses">
        <p className="muted small">Every few minutes the gateway sends the newest log hash to a public timestamp authority, which signs
          “this existed at this time”. Rewriting anything before a witnessed entry, even by the gateway's operator, no longer matches.
          Check a token yourself: <code>openssl ts -verify -digest &lt;hash&gt; -in anchor.tsr -CAfile root.pem</code>.</p>
        {anchors.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th className="num">Covers up to</th><th>Witnessed at</th><th>Authority</th><th>Hash</th><th>Token</th></tr></thead>
              <tbody>
                {anchors.data.slice(0, 8).map((a) => (
                  <tr key={a.id}>
                    <td className="num">#{a.seq}</td>
                    <td>{when(a.gen_time)}</td>
                    <td>{a.tsa_name}</td>
                    <td><Hash value={a.head_hash} n={12} /></td>
                    <td><a href={`/api/anchors/${a.id}.tsr`}>anchor-{a.id}.tsr</a></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="muted">No outside witness yet.</p>}
        <div className="actions">
          <button disabled={anchoring} onClick={async () => { setAnchoring(true); try { await api.anchorNow(); await anchors.refresh() } finally { setAnchoring(false) } }}>
            {anchoring ? 'Asking the timestamp authority…' : 'Witness now'}
          </button>
        </div>
      </Card>
      <Card>
        <div className="table-wrap">
          <table className="log">
            <thead><tr><th className="num">#</th><th>When</th><th>Event</th><th>Subject</th><th>Hash</th><th>Prev</th></tr></thead>
            <tbody>
              {(log.data ?? []).map((e) => (
                <Fragment key={e.seq}>
                  <tr className={open === e.seq ? 'open' : ''} onClick={() => setOpen(open === e.seq ? null : e.seq)}>
                    <td className="num">{e.seq}</td>
                    <td>{when(e.at)}</td>
                    <td><span className="event">{e.event.replaceAll('_', ' ')}</span></td>
                    <td>
                      {e.purchase_id ? <Link href={`/purchases/${e.purchase_id}`}>{e.purchase_id}</Link>
                        : e.intent_id ? <Link href={`/intents/${e.intent_id}`}>{e.intent_id}</Link> : '—'}
                    </td>
                    <td><Hash value={e.hash} n={10} /></td>
                    <td><Hash value={e.prev_hash} n={10} /></td>
                  </tr>
                  {open === e.seq && (
                    <tr className="payload"><td colSpan={6}><pre>{JSON.stringify(e.payload, null, 2)}</pre></td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        {log.data?.length === 0 && <p className="muted">No entries.</p>}
      </Card>
    </Page>
  )
}
