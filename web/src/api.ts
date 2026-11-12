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
  user_signed?: boolean
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
    flagged_skus?: string[]
  } | null
  injection: { score: number; per_text: number[]; model: string } | null
  thresholds: Record<string, number>
  reasons: string[]
}
/** A cart the checkout guard read from a normal shop's checkout (not signed by the shop). */
export type PageEvidence = {
  shop_url: string
  page_url: string
  reader: string
  shipping: Money | null
  tax: Money | null
  discount: Money | null
  total: Money
  screenshot_sha256?: string | null
  note?: string
}

/** Payment made at a shop's own checkout, recorded by the gateway after the guard saw PayPal return. */
export type PaymentConfirmation = {
  paypal_order_id: string
  checked_with_paypal: boolean
  status?: string | null
  amount?: string | null
  currency?: string | null
  capture_id?: string | null
  confirmed_at: string
  detail?: string
  signature_valid?: boolean
}

/** A money-moving call to PayPal's own agent tools, held by the PayPal guard for the person's approval. */
export type PaypalAction = {
  action_id: string
  agent_id: string | null
  tool: string
  arguments: Record<string, unknown>
  args_hash: string
  summary: string
  impact: 'money' | 'account'
  reason: string | null
  status: string
  created_at: string
  approval_expires_at: string | null
  decided_by: string | null
  user_signed: boolean
  result: { text?: string } | null
  history?: { seq: number; at: string; event: string }[]
}

/** Judge mode (DEMO_MODE on the gateway): a scripted demo agent the person drives from the Inbox. */
export type DemoState = {
  enabled: boolean
  agent_name: string
  request: string
  verifier: string
  permissions: { intent_id: string; status: string; created_at: string }[]
  scenarios: { id: string; label: string; expect: string }[]
}

/** Something waiting for the person: a permission to review or a cart held for approval. */
export type Waiting = { kind: 'intent' | 'cart' | 'paypal_action'; id: string; path: string; title: string; text: string; created_at: string }

/** One exchange with a model check, exactly as sent and received (kept for later inspection). */
export type ModelExchange = {
  started_at: string
  latency_ms: number
  attempt: number
  request: { model?: string; messages?: { role: string; content: string }[] }
  status: number
  response_text: string
}

export type Purchase = {
  purchase_id: string
  intent_id: string
  agent_id?: string | null
  merchant_id: string
  cart_source?: 'merchant_signed' | 'page' | 'catalog'
  page_cart?: PageEvidence | null
  page_screenshot_png_b64?: string
  model_exchanges?: ModelExchange[]
  payment_confirmation?: { confirmation: PaymentConfirmation } | null
  checkout_permit?: { permit: { expires_at: string; total: string; currency: string; shop: string } } | null
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
  user_signed?: boolean
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
  user_signatures?: Record<string, { valid: boolean; detail: string }>
  witness?: Witness
  payment_confirmation?: PaymentConfirmation | null
  merchant_signature?: { present: boolean; valid: boolean | null; key_id?: string; detail?: string }
  ap2?: { present: boolean; valid: boolean | null; checks: { name: string; ok: boolean }[]; spec?: string; not_implemented?: string[]; has_payment_receipt?: boolean }
  receipt?: Record<string, unknown> & { lines?: Line[]; total?: string; currency?: string }
}

export type Witness = {
  anchored: boolean
  valid: boolean | null
  detail?: string
  gen_time?: string
  tsa_name?: string
  trusted_by?: string
  anchor_id?: number
  anchor_seq?: number
}

export type Anchor = { id: number; seq: number; head_hash: string; tsa_url: string; tsa_name: string | null; gen_time: string; token_url: string }

export type PasskeyStatus = {
  enabled: boolean
  required: boolean
  mode: string
  passkeys: { credential_id: string; label: string | null; created_at: string }[]
}

export type ApprovalOptions =
  | { passkey: false; required: boolean }
  | { passkey: true; required: true; options: Record<string, unknown>; statement: Record<string, unknown> }

export type Agent = {
  agent_id: string
  name: string
  key_hint: string
  status: 'active' | 'revoked'
  kind: 'mcp' | 'browser'
  spend_cap: string | null
  currency: string
  spent_30d: string
  created_by: string | null
  user_signed: boolean
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

export type Agents = { agents: Agent[]; paused: boolean; paused_at?: string; paused_by?: string }

export type NewAgent = { name: string; spend_cap: string | null; currency?: string; kind?: 'mcp' | 'browser' }

export type SessionInfo = {
  id: string
  method: 'password' | 'passkey'
  created_at: string
  last_seen_at: string
  expires_at: string
  user_agent: string | null
  ip: string | null
  current: boolean
}

// Sign-in lives in an HttpOnly cookie set by the gateway: page scripts never see the token.
// This module only tracks whether the server says we are signed in.
export type AuthState = 'unknown' | 'in' | 'out'
let authState: AuthState = 'unknown'

export function getAuth(): AuthState {
  return authState
}

export function setAuth(state: AuthState) {
  if (state === authState) return
  authState = state
  window.dispatchEvent(new Event('auth'))
}

try {
  localStorage.removeItem('guardrail.token') // tokens from before Nov 1 are no longer used
} catch {
  /* storage unavailable */
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const PUBLIC = ['/api/login', '/api/login/passkey', '/api/login/passkey/options']

// Fallback when the cookie does not reach the gateway (e.g. a hosting proxy that drops
// cookies): keep the session token for this tab only and send it as a Bearer token.
const TAB_KEY = 'guardrail.tab-session'
let tabToken: string | null = (() => {
  try {
    return sessionStorage.getItem(TAB_KEY)
  } catch {
    return null
  }
})()

function setTabToken(token: string | null) {
  tabToken = token
  try {
    if (token) sessionStorage.setItem(TAB_KEY, token)
    else sessionStorage.removeItem(TAB_KEY)
  } catch {
    /* storage unavailable: the token lives in memory only */
  }
}

/** After signing in: use the cookie if it works, otherwise this tab's copy of the token. */
async function adoptSession(token: string) {
  setTabToken(null)
  const viaCookie = await fetch('/api/session', { credentials: 'same-origin' })
  if (!viaCookie.ok) setTabToken(token)
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  // The extra header proves the request comes from this app: another website cannot add it
  // without the browser asking the gateway first, so it cannot act with your cookie.
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Guardrail-Request': '1' }
  if (tabToken && !PUBLIC.includes(path)) headers.Authorization = `Bearer ${tabToken}`
  const res = await fetch(path, {
    method, headers, credentials: 'same-origin', body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 && !PUBLIC.includes(path)) {
    setTabToken(null)
    setAuth('out')
  }
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

/** Ask the server whether this browser is signed in (on load and after focus). */
export async function checkSession() {
  try {
    await call('GET', '/api/session')
    setAuth('in')
  } catch {
    setAuth('out')
  }
}

export const api = {
  login: async (password: string) => {
    const r = await call<{ user: string; token: string }>('POST', '/api/login', { password })
    await adoptSession(r.token)
    return r
  },
  passkeySignInOptions: () => call<ApprovalOptions & { nonce: string }>('POST', '/api/login/passkey/options'),
  passkeySignIn: async (nonce: string, credential: unknown) => {
    const r = await call<{ user: string; token: string }>('POST', '/api/login/passkey', { nonce, credential })
    await adoptSession(r.token)
    return r
  },
  logout: async () => {
    try {
      return await call<{ ok: boolean }>('POST', '/api/logout')
    } finally {
      setTabToken(null)
    }
  },
  sessions: () => call<SessionInfo[]>('GET', '/api/sessions'),
  endSession: (id: string) => call<{ ok: boolean }>('POST', `/api/sessions/${id}/end`),
  endOtherSessions: () => call<{ ended: number }>('POST', '/api/sessions/end-others'),
  waiting: () => call<Waiting[]>('GET', '/api/waiting'),
  demoState: () => call<DemoState>('GET', '/api/demo'),
  paypalActions: () => call<PaypalAction[]>('GET', '/api/paypal-actions'),
  paypalAction: (id: string) => call<PaypalAction>('GET', `/api/paypal-actions/${id}`),
  paypalActionOptions: (id: string) => call<ApprovalOptions>('POST', `/api/paypal-actions/${id}/approval-options`),
  decidePaypalAction: (id: string, approve: boolean, args_hash: string, webauthn?: unknown) =>
    call<PaypalAction>('POST', `/api/paypal-actions/${id}/decision`, { approve, args_hash, webauthn }),
  demoPropose: () => call<Intent>('POST', '/api/demo/permission'),
  demoBuy: (intent_id: string, scenario: string) => call<Purchase>('POST', '/api/demo/buy', { intent_id, scenario }),
  intents: () => call<Intent[]>('GET', '/api/intents'),
  intent: (id: string) => call<Intent>('GET', `/api/intents/${id}`),
  decideIntent: (id: string, approve: boolean, webauthn?: unknown) =>
    call<Intent>('POST', `/api/intents/${id}/decision`, { approve, webauthn }),
  revokeIntent: (id: string) => call<Intent>('POST', `/api/intents/${id}/revoke`),
  intentApprovalOptions: (id: string) =>
    call<ApprovalOptions>('POST', `/api/intents/${id}/approval-options`),
  purchases: (intentId?: string) =>
    call<Purchase[]>('GET', `/api/purchases${intentId ? `?intent_id=${intentId}` : ''}`),
  purchase: (id: string) => call<Purchase>('GET', `/api/purchases/${id}`),
  decidePurchase: (id: string, approve: boolean, cartHash: string, webauthn?: unknown) =>
    call<Purchase>('POST', `/api/purchases/${id}/decision`, { approve, cart_hash: cartHash, webauthn }),
  purchaseApprovalOptions: (id: string) =>
    call<ApprovalOptions>('POST', `/api/purchases/${id}/approval-options`),
  passkeys: () => call<PasskeyStatus>('GET', '/api/passkeys'),
  passkeyRegistrationOptions: () => call<Record<string, unknown>>('POST', '/api/passkeys/register/options'),
  registerPasskey: (credential: unknown, label: string) =>
    call<PasskeyStatus>('POST', '/api/passkeys/register', { credential, label }),
  agents: () => call<Agents>('GET', '/api/agents'),
  agentOptions: (a: NewAgent) => call<ApprovalOptions>('POST', '/api/agents/options', a),
  createAgent: (a: NewAgent, webauthn?: unknown) =>
    call<Agent & { key: string; note: string }>('POST', '/api/agents', { ...a, webauthn }),
  revokeAgent: (id: string) => call<Agent>('POST', `/api/agents/${id}/revoke`),
  stopAgents: () => call<Agents>('POST', '/api/agents/stop'),
  resumeOptions: () => call<ApprovalOptions>('POST', '/api/agents/resume/options'),
  resumeAgents: (webauthn?: unknown) => call<Agents>('POST', '/api/agents/resume', { webauthn }),
  log: (q: { intent_id?: string; purchase_id?: string } = {}) =>
    call<LogEntry[]>('GET', `/api/log?${new URLSearchParams(q as Record<string, string>)}`),
  anchors: () => call<Anchor[]>('GET', '/api/anchors'),
  anchorNow: () => call<Anchor | { anchored: false; reason: string }>('POST', '/api/log/anchor'),
  verifyLog: () => call<{ valid: boolean; first_bad_seq: number | null; entries: number }>(
    'GET', '/api/log/verify'),
  verifyReceipt: (id: string) => call<ReceiptCheck>('GET', `/api/receipts/${id}/verify`),
}
