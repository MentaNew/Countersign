import { useEffect, useState } from 'react'
import { getToken, setToken } from './api'
import Inbox from './pages/Inbox'
import IntentPage from './pages/IntentPage'
import LogPage from './pages/LogPage'
import Login from './pages/Login'
import PurchasePage from './pages/PurchasePage'
import ReceiptPage from './pages/ReceiptPage'
import { Link, match, useLocation } from './router'
import SellerApp from './seller/SellerApp'

function useAuthed() {
  const [authed, setAuthed] = useState(!!getToken())
  useEffect(() => {
    const on = () => setAuthed(!!getToken())
    window.addEventListener('auth', on)
    return () => window.removeEventListener('auth', on)
  }, [])
  return authed
}

function NotFound() {
  return <main className="page"><h1>Not found</h1><Link href="/">Back to inbox</Link></main>
}

export default function App() {
  const { path, query } = useLocation()
  const authed = useAuthed()

  // Seller side (dispute desk): separate login, separate server, its own look.
  if (path === '/seller' || path.startsWith('/seller/')) return <SellerApp path={path} />

  // Receipts are public so a seller or dispute analyst can verify them without logging in.
  const receipt = match('/receipts/:id', path)
  if (!authed && !receipt) return <Login />

  let page
  let m: Record<string, string> | null
  if (path === '/') page = <Inbox />
  else if ((m = match('/intents/:id', path))) page = <IntentPage id={m.id} />
  else if ((m = match('/purchases/:id', path))) page = <PurchasePage id={m.id} />
  else if (receipt) page = <ReceiptPage id={receipt.id} />
  else if (path === '/log') page = <LogPage query={query} />
  else page = <NotFound />

  return (
    <div className="shell">
      <nav className="top">
        <Link href="/" className="brand">
          <img src="/favicon.svg" alt="Spend Guardrail" width={22} height={22} /> <span>Spend Guardrail</span>
        </Link>
        {authed && (
          <div className="nav-links">
            <Link href="/" className={path === '/' ? 'active' : ''}>Inbox</Link>
            <Link href="/log" className={path === '/log' ? 'active' : ''}>Audit log</Link>
            <button className="link" onClick={() => setToken(null)}>Sign out</button>
          </div>
        )}
      </nav>
      {page}
    </div>
  )
}
