import { useState, type FormEvent } from 'react'
import { api, setAuth } from '../api'
import { describeError, signInWithPasskey } from '../passkey'

export default function Login() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async (f: () => Promise<unknown>, fallback: string) => {
    setBusy(true)
    setError(null)
    try {
      await f()
      setAuth('in')
    } catch (e) {
      const msg = describeError(e)
      setError(/wrong password/i.test(msg) ? fallback : msg)
    } finally {
      setBusy(false)
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    run(() => api.login(password), 'Wrong password.')
  }

  return (
    <main className="login">
      <form className="card" onSubmit={submit}>
        <div className="brand"><img src="/favicon.svg" alt="" width={26} height={26} /> <h1>Spend Guardrail</h1></div>
        <p className="muted">
          Sign in as the person the agent buys for. Only you can approve what it may buy; the agent
          has no access to this screen.
        </p>
        <button type="button" className="primary" disabled={busy}
          onClick={() => run(signInWithPasskey, 'Passkey sign-in failed.')}>
          Sign in with your passkey
        </button>
        <div className="or"><span>or</span></div>
        <label>
          Password
          <input type="password" value={password} autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="notice bad" role="alert">{error}</div>}
        <button disabled={busy || !password}>Sign in with password</button>
        <p className="muted small">
          Sessions end after 2 hours without activity and after 12 hours at most. Approvals always ask
          for your passkey once one is set up.
        </p>
      </form>
    </main>
  )
}
