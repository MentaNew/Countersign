import { useEffect, useState, type FormEvent } from 'react'
import { Link, match } from '../router'
import { desk, getSellerToken, setSellerToken } from './api'
import DisputePage from './DisputePage'
import DisputesPage from './DisputesPage'

function useSellerAuthed() {
  const [authed, setAuthed] = useState(!!getSellerToken())
  useEffect(() => {
    const on = () => setAuthed(!!getSellerToken())
    window.addEventListener('desk-auth', on)
    return () => window.removeEventListener('desk-auth', on)
  }, [])
  return authed
}

function SellerLogin() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    try {
      setSellerToken((await desk.login(password)).token)
    } catch {
      setError('Wrong password.')
    }
  }
  return (
    <main className="login">
      <form className="card" onSubmit={submit}>
        <div className="side-tag">Seller side</div>
        <h1>Dispute desk</h1>
        <p className="muted">
          For the merchant's staff. Answer PayPal disputes with evidence, including authorization receipts from
          buyers' payment gateways. This is not the buyer's approval inbox.
        </p>
        <label>
          Seller password
          <input type="password" value={password} autoFocus onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="notice bad">{error}</div>}
        <button className="primary" disabled={!password}>Sign in</button>
        <p className="muted small">Demo login: <code>SELLER_PASSWORD</code> from <code>.env</code>.</p>
      </form>
    </main>
  )
}

export default function SellerApp({ path }: { path: string }) {
  const authed = useSellerAuthed()
  if (!authed) return <div className="seller"><SellerLogin /></div>

  const m = match('/seller/disputes/:id', path)
  const page = m ? <DisputePage id={m.id} /> : <DisputesPage />

  return (
    <div className="shell seller">
      <nav className="top">
        <Link href="/seller" className="brand">
          <span className="seller-mark" aria-hidden>⚖</span> <span>Dispute desk</span>
          <span className="side-tag">Seller side</span>
        </Link>
        <div className="nav-links">
          <Link href="/seller" className={path === '/seller' ? 'active' : ''}>Disputes</Link>
          <button className="link" onClick={() => setSellerToken(null)}>Sign out</button>
        </div>
      </nav>
      {page}
    </div>
  )
}
