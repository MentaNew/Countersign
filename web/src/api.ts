// Thin client for the gateway's user-side REST API (proxied by Vite to the gateway).

export type Money = { amount: string; currency: string }
export type MandateItem = { description: string; qty: number; max_unit_price?: Money | null }
export type Mandate = {
  items: MandateItem[]
  max_total: Money
  merchants: string[]
  approval_above?: Money | null
  expires_at: string
  max_purchases: number
}
export type Intent = {
  intent_id: string
  status: 'pending_user' | 'active' | 'rejected' | 'revoked'
  user_text: string
  mandate: Mandate
  summary: string
  created_at: string
  approved_by: string | null
  approved_at: string | null
  signature: string | null
}
export type Line = { sku: string; title: string; unit_price: Money; qty: number; item: number | null }
export type CheckResult = { name: string; passed: boolean; detail: string }
export type Verdict = {
  decision: 'approve' | 'block' | 'escalate'
  hard_checks: CheckResult[]
  semantic: {
    match: { matches: number; partial: number; mismatch: number }
    extra_items: number
    model: string
    rationale: string
  } | null
  injection: { score: number; per_text: number[]; model: string } | null
  thresholds: Record<string, number>
  reasons: string[]
}
export type Purchase = {
  purchase_id: string
  intent_id: string
  merchant_id: string
  lines: Line[]
  total: string
  currency: string
  cart_hash: string
  decision: Verdict['decision']
  status: string
  reasons: string[]
  verdict: Verdict
  context_texts: string[]
  approval_url: string | null
  approval_expires_at: string | null
  approved_by: string | null
  receipt_id: string | null
  paypal_order_id: string | null
  paypal_status: string | null
  payer_action_url: string | null
  error: string | null
  created_at: string
}
export type LogEntry = {
  seq: number
  at: string
  event: string
  intent_id: string | null
  purchase_id: string | null
  payload: Record<string, unknown>
  prev_hash: string
  hash: string
}
export type ReceiptCheck = {
  receipt_id: string
  valid: boolean
  signature_valid?: boolean
  log_anchor_valid?: boolean
  chain_valid?: boolean
  chain_first_bad_seq?: number | null
  chain_entries?: number
  key_id?: string
  reason?: string
  receipt?: Record<string, unknown> & { lines?: Line[]; total?: string; currency?: string }
}

const TOKEN_KEY = 'guardrail.token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable: the session lasts until reload */
  }
  window.dispatchEvent(new Event('auth'))
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined })
  if (res.status === 401 && path !== '/api/login') setToken(null)
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  login: (password: string) => call<{ token: string }>('POST', '/api/login', { password }),
  intents: () => call<Intent[]>('GET', '/api/intents'),
  intent: (id: string) => call<Intent>('GET', `/api/intents/${id}`),
  decideIntent: (id: string, approve: boolean) =>
    call<Intent>('POST', `/api/intents/${id}/decision`, { approve }),
  purchases: (intentId?: string) =>
    call<Purchase[]>('GET', `/api/purchases${intentId ? `?intent_id=${intentId}` : ''}`),
  purchase: (id: string) => call<Purchase>('GET', `/api/purchases/${id}`),
  decidePurchase: (id: string, approve: boolean, cartHash: string) =>
    call<Purchase>('POST', `/api/purchases/${id}/decision`, { approve, cart_hash: cartHash }),
  log: (q: { intent_id?: string; purchase_id?: string } = {}) =>
    call<LogEntry[]>('GET', `/api/log?${new URLSearchParams(q as Record<string, string>)}`),
  verifyLog: () => call<{ valid: boolean; first_bad_seq: number | null; entries: number }>(
    'GET', '/api/log/verify'),
  verifyReceipt: (id: string) => call<ReceiptCheck>('GET', `/api/receipts/${id}/verify`),
}
