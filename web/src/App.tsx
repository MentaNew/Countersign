import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, checkSession, getAuth, setAuth, type AuthState } from './api'
import PurchaseDetail, { CartHeader, usePurchase, type CartTab } from './cart/PurchaseDetail'
import { Icon } from './icons'
import AgentsPage from './pages/AgentsPage'
import IntentPage from './pages/IntentPage'
import LogPage from './pages/LogPage'
import Login from './pages/Login'
import Queue from './pages/Queue'
import ReceiptPage from './pages/ReceiptPage'
import SecurityPage from './pages/SecurityPage'
import { Link, match, navigate, replace, useLocation } from './router'
import SellerApp from './seller/SellerApp'
import { Notice } from './ui'
import { useWaiting } from './waiting'

function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>(getAuth())
  useEffect(() => {
    const on = () => setState(getAuth())
    window.addEventListener('auth', on)
    checkSession()
    // Re-check when the tab comes back: the session may have expired or been ended elsewhere.
    const onFocus = () => document.visibilityState === 'visible' && checkSession()
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      window.removeEventListener('auth', on)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [])
  return state
}

async function signOut() {
  try {
    await api.logout()
  } finally {
    setAuth('out')
  }
}

/** Free hosting sleeps when idle: say so instead of a blank "Loading…" that looks broken. */
function Waking() {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setSlow(true), 3000)
    return () => clearTimeout(t)
  }, [])
  return (
    <main className="login">
      <div className="card" style={{ maxWidth: 420, textAlign: 'center' }}>
        <img src="/favicon.svg" alt="" width={28} height={28} />
        <p style={{ margin: '10px 0 4px', fontWeight: 600 }}>{slow ? 'Waking the server up…' : 'Loading…'}</p>
        {slow && <p className="muted small" style={{ margin: 0 }}>This demo runs on free hosting that sleeps when nobody uses it.
          The first visit can take up to a minute; after that it is instant.</p>}
      </div>
    </main>
  )
}

function NotFound() {
  return <main className="page"><div className="empty"><strong>Page not found</strong><Link href="/">Back to the inbox</Link></div></main>
}

/** Full-page cart, for deep work and links from the agent ("approval_url"). */
function PurchaseFullPage({ id }: { id: string }) {
  const [tab, setTab] = useState<CartTab>('decision')
  const { q } = usePurchase(id)
  const agents = useAgentsNames()
  return (
    <main className="page">
      {q.data ? <div className="dhead card" style={{ position: 'relative' }}><CartHeader p={q.data} agentName={agents[q.data.agent_id ?? '']} /></div>
        : q.error ? <Notice tone="bad">{q.error}</Notice> : null}
      <div className="card" style={{ padding: 0, background: 'var(--bg)' }}>
        <PurchaseDetail id={id} tab={tab} onTab={setTab} />
      </div>
    </main>
  )
}

function useAgentsNames(): Record<string, string> {
  const [names, setNames] = useState<Record<string, string>>({})
  useEffect(() => {
    api.agents().then((a) => setNames(Object.fromEntries(a.agents.map((x) => [x.agent_id, x.name])))).catch(() => {})
  }, [])
  return names
}

function Shell({ path, crumbs, children, waiting }: { path: string; crumbs: ReactNode; children: ReactNode; waiting: number }) {
  const [menu, setMenu] = useState(false)
  const search = useRef<HTMLInputElement>(null)
  const { query } = useLocation()
  useEffect(() => setMenu(false), [path])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(t?.tagName)) {
        e.preventDefault()
        search.current?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])
  const nav = (href: string, icon: string, label: string, extra?: ReactNode, active?: boolean) => (
    <Link href={href} className={active ?? path === href ? 'active' : ''}><Icon name={icon} />{label}{extra}</Link>
  )
  const onSearch = (v: string) => {
    const u = new URL(window.location.origin + '/')
    const keep = path === '/' ? new URL(window.location.href).searchParams : new URLSearchParams()
    keep.delete('open')
    if (v) keep.set('q', v)
    else keep.delete('q')
    const to = `${u.pathname}${keep.toString() ? `?${keep}` : ''}`
    if (path === '/') replace(to)
    else navigate(to)
  }
  return (
    <div className={`app ${menu ? 'menu-open' : ''}`}>
      <aside className="side">
        <Link href="/" className="brand"><img src="/favicon.svg" alt="" width={22} height={22} /> Spend Guardrail</Link>
        <nav className="nav" aria-label="Main">
          {nav('/', 'inbox', 'Inbox', waiting > 0 && <span className="count" aria-label={`${waiting} waiting`}>{waiting}</span>,
            path === '/' || path.startsWith('/purchases') || path.startsWith('/intents'))}
          {nav('/agents', 'agent', 'Agents')}
          {nav('/log', 'log', 'Audit log')}
          {nav('/security', 'key', 'Security')}
        </nav>
        <div className="side-foot">
          <Link href="/seller" className="navlink" style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--text-2)' }}>
            <Icon name="seller" /> Seller desk
          </Link>
          <button className="navlink link" style={{ color: 'var(--text-2)', display: 'flex', gap: 8, alignItems: 'center' }} onClick={signOut}>
            <Icon name="logout" /> Sign out
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="iconbtn menu-btn" aria-label="Menu" onClick={() => setMenu((m) => !m)}><Icon name="menu" /></button>
          <nav className="crumbs" aria-label="Breadcrumb">{crumbs}</nav>
          <label className="search">
            <Icon name="search" />
            <input ref={search} aria-label="Search carts and permissions" placeholder="Search carts and permissions"
              defaultValue={path === '/' ? (query.get('q') ?? '') : ''} key={path}
              onChange={(e) => onSearch(e.target.value)} />
            <kbd>/</kbd>
          </label>
        </header>
        {children}
      </div>
    </div>
  )
}

export default function App() {
  const { path, query } = useLocation()
  const auth = useAuth()
  const authed = auth === 'in'
  const waiting = useWaiting(authed && !path.startsWith('/seller'))

  // Seller side (dispute desk): separate login, separate server, its own look.
  if (path === '/seller' || path.startsWith('/seller/')) return <SellerApp path={path} />

  // Receipts are public so a seller or dispute analyst can verify them without logging in.
  const receipt = match('/receipts/:id', path)
  if (auth === 'unknown' && !receipt) return <Waking />
  if (!authed && !receipt) return <Login />
  if (!authed && receipt) return <ReceiptPage id={receipt.id} />

  let page: ReactNode
  let crumb: ReactNode
  let m: Record<string, string> | null
  const inbox = <Link href="/">Inbox</Link>
  if (path === '/') { page = <Queue />; crumb = <span className="here">Inbox</span> }
  else if ((m = match('/intents/:id', path))) { page = <IntentPage id={m.id} />; crumb = <>{inbox} / <span className="here">Permission</span></> }
  else if ((m = match('/purchases/:id', path))) { page = <PurchaseFullPage id={m.id} />; crumb = <>{inbox} / <span className="here">Cart</span></> }
  else if (receipt) { page = <ReceiptPage id={receipt.id} />; crumb = <>{inbox} / <span className="here">Receipt</span></> }
  else if (path === '/log') { page = <LogPage query={query} />; crumb = <span className="here">Audit log</span> }
  else if (path === '/security') { page = <SecurityPage />; crumb = <span className="here">Security</span> }
  else if (path === '/agents') { page = <AgentsPage />; crumb = <span className="here">Agents</span> }
  else { page = <NotFound />; crumb = <span className="here">Not found</span> }

  return <Shell path={path} crumbs={crumb} waiting={waiting.length}>{page}</Shell>
}
