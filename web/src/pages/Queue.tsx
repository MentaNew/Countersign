// The landing page: a work queue of carts and permissions, with saved views, a filter, keyboard triage
// (j/k move, Enter open, Esc close, / search) and a drawer for the selected item (?open=<id>).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Agent, type Intent, type PaypalAction, type Purchase } from '../api'
import PaypalActionDetail from '../cart/PaypalActionDetail'
import PurchaseDetail, { CartHeader, type CartTab } from '../cart/PurchaseDetail'
import { Icon } from '../icons'
import { Link, replace, useLocation } from '../router'
import { Badge, Monogram, Notice, Seal, ago, plainReason, shopName, usePoll, when } from '../ui'
import { IntentDetail } from './IntentPage'
import DemoPanel from './DemoPanel'
import { SetupChecklist } from './Inbox'

type Row = {
  kind: 'cart' | 'permission' | 'paypal_action'
  id: string
  at: string
  title: string
  shop: string | null
  agent: string
  amount: number | null
  currency: string
  status: string
  why: string
  needs: boolean
  human: boolean
  signed?: boolean
  search: string
}

type View = 'needs' | 'approved' | 'stopped' | 'permissions' | 'all'
const VIEWS: [View, string][] = [
  ['needs', 'Needs you'], ['approved', 'Approved'], ['stopped', 'Stopped'], ['permissions', 'Permissions'], ['all', 'All'],
]
const APPROVED = ['authorized', 'ordered', 'captured', 'payment_reported', 'approved', 'executing', 'done']
const STOPPED = ['blocked', 'rejected', 'failed', 'approval_expired', 'cancelled']
const inView = (r: Row, v: View) =>
  v === 'needs' ? r.needs
    : v === 'approved' ? r.kind !== 'permission' && APPROVED.includes(r.status)
      : v === 'stopped' ? r.kind !== 'permission' && STOPPED.includes(r.status)
        : v === 'permissions' ? r.kind === 'permission'
          : r.kind !== 'permission'

function toRows(purchases: Purchase[], intents: Intent[], agents: Agent[], actions: PaypalAction[] = []): Row[] {
  const name = (id: string | null | undefined) => agents.find((a) => a.agent_id === id)?.name ?? (id ? id.slice(0, 12) : 'agent')
  const carts: Row[] = purchases.map((p) => ({
    kind: 'cart', id: p.purchase_id, at: p.created_at, title: shopName(p.merchant_id), shop: p.merchant_id,
    agent: name(p.agent_id), amount: Number(p.total), currency: p.currency, status: p.status,
    why: p.status === 'pending_approval' || p.decision !== 'approve' ? [...new Set(p.reasons.map(plainReason))].join(' · ') : `${p.lines[0]?.qty ?? ''} × ${p.lines[0]?.title ?? ''}${p.lines.length > 1 ? ` +${p.lines.length - 1} more` : ''}`,
    needs: p.status === 'pending_approval', human: !!p.approved_by, signed: p.user_signed,
    search: `${p.purchase_id} ${p.merchant_id} ${name(p.agent_id)} ${p.reasons.join(' ')} ${p.lines.map((l) => l.title).join(' ')}`.toLowerCase(),
  }))
  const perms: Row[] = intents.map((i) => ({
    kind: 'permission', id: i.intent_id, at: i.created_at, title: 'Permission', shop: null,
    agent: name((i as Intent & { agent_id?: string }).agent_id), amount: Number(i.mandate.max_total.amount),
    currency: i.mandate.max_total.currency, status: i.status, why: i.user_text, needs: i.status === 'pending_user',
    human: true, signed: i.user_signed,
    search: `${i.intent_id} ${i.user_text} ${i.mandate.merchants.join(' ')}`.toLowerCase(),
  }))
  const acts: Row[] = actions.map((a) => ({
    kind: 'paypal_action', id: a.action_id, at: a.created_at, title: a.summary, shop: null,
    agent: name(a.agent_id), amount: null, currency: '', status: a.status,
    why: a.impact === 'money' ? 'PayPal action · money moves' : 'PayPal action · no money moves',
    needs: a.status === 'pending_approval', human: !!a.decided_by, signed: a.user_signed,
    search: `${a.action_id} paypal ${a.tool} ${a.summary}`.toLowerCase(),
  }))
  return [...carts, ...perms, ...acts].sort((a, b) => b.at.localeCompare(a.at))
}

function Drawer({ row, agents, onClose }: { row: Row | null; agents: Agent[]; onClose: () => void }) {
  // The tab belongs to the item: a newly opened item starts on Decision.
  const [picked, setPicked] = useState<{ id: string; tab: CartTab } | null>(null)
  const tab: CartTab = picked && picked.id === row?.id ? picked.tab : 'decision'
  const setTab = (t: CartTab) => row && setPicked({ id: row.id, tab: t })
  const purchase = usePoll(() => (row?.kind === 'cart' ? api.purchase(row.id) : Promise.resolve(null)), [row?.id], 5000)
  const agentName = (id: string | null | undefined) => agents.find((a) => a.agent_id === id)?.name
  return (
    <aside className={`drawer ${row ? 'on' : ''}`} aria-hidden={!row} aria-label="Details">
      {row && (
        <>
          <div className="dhead">
            {row.kind === 'cart' && purchase.data ? <CartHeader p={purchase.data} agentName={agentName(purchase.data.agent_id)} /> : (
              <>
                <div className="row1"><span className="mono-tile lg"><Icon name={row.kind === 'permission' ? 'permission' : 'receipt'} /></span>
                  <div><div className="title">{row.kind === 'permission' ? 'Permission' : row.title}</div><div className="muted small"><code>{row.id}</code></div></div>
                  <Badge value={row.status} /></div>
                <div className="meta"><span><Icon name="agent" /> {row.agent}</span><span><Icon name="clock" /> {when(row.at)}</span></div>
              </>
            )}
            <div className="tools" style={{ position: 'absolute', right: 12, top: 12, display: 'flex', gap: 2 }}>
              {row.kind !== 'paypal_action' && <Link className="button iconbtn" href={row.kind === 'cart' ? `/purchases/${row.id}` : `/intents/${row.id}`} title="Open full page"><Icon name="expand" /></Link>}
              <button className="iconbtn" onClick={onClose} title="Close (Esc)" aria-label="Close"><Icon name="close" /></button>
            </div>
            {row.kind !== 'cart' && <div style={{ height: 10 }} />}
          </div>
          {row.kind === 'cart' ? <PurchaseDetail id={row.id} tab={tab} onTab={setTab} />
            : row.kind === 'paypal_action' ? <PaypalActionDetail id={row.id} />
              : <div className="dbody"><IntentDetail id={row.id} /></div>}
        </>
      )}
    </aside>
  )
}

export default function Queue() {
  const { query } = useLocation()
  const purchases = usePoll(() => api.purchases(), [], 4000)
  const intents = usePoll(() => api.intents(), [], 4000)
  const agents = usePoll(() => api.agents(), [], 30000)
  const actions = usePoll(() => api.paypalActions(), [], 4000)
  const [chosen, setView] = useState<View | null>(() => (query.get('view') as View) || null)
  const [kbRaw, setKb] = useState(0)
  const q = (query.get('q') ?? '').toLowerCase()
  const openId = query.get('open')
  const wrapRef = useRef<HTMLDivElement>(null)

  const rows = useMemo(
    () => toRows(purchases.data ?? [], intents.data ?? [], agents.data?.agents ?? [], actions.data ?? []),
    [purchases.data, intents.data, agents.data, actions.data],
  )
  const counts = useMemo(() => Object.fromEntries(VIEWS.map(([v]) => [v, rows.filter((r) => inView(r, v)).length])) as Record<View, number>, [rows])
  // Until you pick a tab: "Needs you" when something waits, otherwise "All carts".
  const view: View = chosen ?? (counts.needs || !purchases.data ? 'needs' : 'all')
  const visible = useMemo(() => rows.filter((r) => inView(r, view) && (!q || r.search.includes(q))), [rows, view, q])
  const kb = Math.min(kbRaw, Math.max(0, visible.length - 1))
  const openRow = rows.find((r) => r.id === openId) ?? null

  const setParam = useCallback((k: string, v: string | null) => {
    const u = new URL(window.location.href)
    if (v) u.searchParams.set(k, v)
    else u.searchParams.delete(k)
    replace(u.pathname + u.search)
  }, [])
  const open = useCallback((id: string | null) => setParam('open', id), [setParam])

  useEffect(() => {
    document.body.classList.toggle('drawer-open', !!openRow)
    return () => document.body.classList.remove('drawer-open')
  }, [openRow])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t && ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) {
        if (e.key === 'Escape') t.blur()
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'j' || e.key === 'k') {
        e.preventDefault()
        const cur = openRow ? visible.findIndex((r) => r.id === openRow.id) : kb
        const next = Math.max(0, Math.min(visible.length - 1, (cur < 0 ? kb : cur) + (e.key === 'j' ? 1 : -1)))
        setKb(next)
        if (openRow && visible[next]) open(visible[next].id)
        const el = wrapRef.current?.querySelector(`tbody tr:nth-child(${next + 1})`) as HTMLElement | null
        const box = wrapRef.current
        if (el && box) {
          const b = box.getBoundingClientRect(), r = el.getBoundingClientRect()
          if (r.top < b.top + 36) box.scrollTop -= b.top + 36 - r.top
          else if (r.bottom > b.bottom) box.scrollTop += r.bottom - b.bottom
        }
      } else if (e.key === 'Enter' && visible[kb]) {
        open(visible[kb].id)
      } else if (e.key === 'Escape' && openRow) {
        open(null)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [visible, kb, openRow, open])

  const loading = !purchases.data || !intents.data
  const error = purchases.error ?? intents.error
  const needs = counts.needs

  return (
    <main className="page wide">
      <header className="page-head">
        <div>
          <h1>Inbox</h1>
          <p className="lede">{needs ? `${needs} waiting for your decision` : 'Nothing waiting for you'} · {counts.all} carts and PayPal actions, {counts.permissions} {counts.permissions === 1 ? 'permission' : 'permissions'} so far</p>
        </div>
      </header>
      {error && <Notice tone="bad">{error}</Notice>}
      <DemoPanel onOpen={(id) => { setView('all'); setParam('view', 'all'); open(id) }} />
      <SetupChecklist />

      <div className="views" role="tablist">
        {VIEWS.map(([v, label]) => (
          <button key={v} role="tab" aria-selected={view === v} className={view === v ? 'on' : ''}
            onClick={() => { setView(v); setKb(0); setParam('view', v) }}>
            {label} <span className={`n ${v === 'needs' && counts[v] ? 'red' : ''}`}>{counts[v]}</span>
          </button>
        ))}
      </div>
      <div className="toolbar">
        <label className="filter">
          <Icon name="search" />
          <input aria-label="Filter" placeholder="Filter by shop, agent, item, reason…" value={query.get('q') ?? ''}
            onChange={(e) => setParam('q', e.target.value || null)} />
        </label>
        <span className="hints"><span><kbd>j</kbd> <kbd>k</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span></span>
      </div>

      {loading ? <p className="muted">Loading…</p> : !visible.length ? (
        <div className="empty">
          <strong>{q ? 'Nothing matches this filter' : view === 'needs' ? 'Nothing needs your decision' : 'Nothing here yet'}</strong>
          {view === 'needs' && !q ? 'When an agent proposes a permission or a cart is held, it appears here (and you get a notification if you turned them on).' : 'Try another view or clear the filter.'}
        </div>
      ) : (
        <div className="gridcard">
          <div className="gridwrap" ref={wrapRef}>
            <table className="grid">
              <thead><tr>
                <th>Status</th><th>What</th><th className="opt">Why</th><th className="opt">Agent</th><th className="num">Amount</th>
                <th className="opt">Approval</th><th className="opt-sm">When</th>
              </tr></thead>
              <tbody>
                {visible.map((r, i) => (
                  <tr key={r.id} className={`${r.id === openId ? 'open' : ''} ${i === kb && r.id !== openId ? 'kb' : ''}`}
                    onClick={() => { setKb(i); open(r.id) }}>
                    <td><Badge value={r.status} /></td>
                    <td><span className="who">
                      {r.kind === 'cart' ? <Monogram name={r.shop ?? ''} /> : <span className="mono-tile"><Icon name={r.kind === 'permission' ? 'permission' : 'receipt'} /></span>}
                      <span className="what-cell"><span>{r.title}</span><span className="sub-why">{r.why.split(' · ')[0]}</span></span></span></td>
                    <td className="why opt" title={r.why}>{r.why}</td>
                    <td className="opt">{r.agent}</td>
                    <td className="num">{r.amount !== null ? `${r.amount.toFixed(2)} ${r.currency}` : '—'}{r.kind === 'permission' && <div className="muted small">budget</div>}</td>
                    <td className="opt">{r.kind !== 'permission'
                      ? (APPROVED.includes(r.status) ? <Seal human={r.human} signed={r.signed} short /> : <span className="muted">—</span>)
                      : (r.status === 'active' ? <Seal human signed={r.signed} short /> : <span className="muted">—</span>)}</td>
                    <td className="muted opt-sm" title={when(r.at)}>{ago(r.at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="gridfoot">
            <span>{visible.length} of {counts[view]} {view === 'permissions' ? 'permissions' : view === 'needs' ? 'waiting' : 'items'}</span>
            <span>Approval: <Icon name="fingerprint" /> signed with your passkey · <Icon name="key" /> password only · <Icon name="bolt" /> automatic within your rules</span>
          </div>
        </div>
      )}
      <Drawer row={openRow} agents={agents.data?.agents ?? []} onClose={() => open(null)} />
    </main>
  )
}
