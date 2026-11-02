import { useEffect, useState } from 'react'
import { api, checkSession, getAuth, setAuth, type AuthState } from './api'
import AgentsPage from './pages/AgentsPage'
import Inbox from './pages/Inbox'
import IntentPage from './pages/IntentPage'
import LogPage from './pages/LogPage'
import Login from './pages/Login'
import PurchasePage from './pages/PurchasePage'
import ReceiptPage from './pages/ReceiptPage'
import SecurityPage from './pages/SecurityPage'
import { Link, match, useLocation } from './router'
import SellerApp from './seller/SellerApp'
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

function NotFound() {
  return <main className="page"><h1>Not found</h1><Link href="/">Back to inbox</Link></main>
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
  if (auth === 'unknown' && !receipt) return <main className="login"><p className="muted">Loading…</p></main>
  if (!authed && !receipt) return <Login />

  let page
  let m: Record<string, string> | null
  if (path === '/') page = <Inbox />
  else if ((m = match('/intents/:id', path))) page = <IntentPage id={m.id} />
  else if ((m = match('/purchases/:id', path))) page = <PurchasePage id={m.id} />
  else if (receipt) page = <ReceiptPage id={receipt.id} />
  else if (path === '/log') page = <LogPage query={query} />
  else if (path === '/security') page = <SecurityPage />
  else if (path === '/agents') page = <AgentsPage />
  else page = <NotFound />

  return (
    <div className="shell">
      <nav className="top">
        <Link href="/" className="brand">
          <img src="/favicon.svg" alt="Spend Guardrail" width={22} height={22} /> <span>Spend Guardrail</span>
        </Link>
        {authed && (
          <div className="nav-links">
            <Link href="/" className={path === '/' ? 'active' : ''}>
              Inbox{waiting.length > 0 && <span className="count" aria-label={`${waiting.length} waiting`}>{waiting.length}</span>}
            </Link>
            <Link href="/log" className={path === '/log' ? 'active' : ''}>Audit log</Link>
            <Link href="/agents" className={path === '/agents' ? 'active' : ''}>Agents</Link>
            <Link href="/security" className={path === '/security' ? 'active' : ''}>Security</Link>
            <button className="link" onClick={signOut}>Sign out</button>
          </div>
        )}
      </nav>
      {page}
    </div>
  )
}
