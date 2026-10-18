import { useState, type FormEvent } from 'react'
import { api, setToken } from '../api'

export default function Login() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      setToken((await api.login(password)).token)
    } catch {
      setError('Wrong password.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login">
      <form className="card" onSubmit={submit}>
        <h1>Spend Guardrail</h1>
        <p className="muted">
          Sign in as the person the agent buys for. Only you can approve what it may buy; the agent
          has no access to this screen.
        </p>
        <label>
          Password
          <input type="password" value={password} autoFocus onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="notice bad">{error}</div>}
        <button className="primary" disabled={busy || !password}>Sign in</button>
        <p className="muted small">Demo login: the <code>USER_PASSWORD</code> value from <code>.env</code>.</p>
      </form>
    </main>
  )
}
