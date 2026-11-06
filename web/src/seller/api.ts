// Client for the seller-side dispute desk (a separate server, proxied at /desk).
// Its login token is separate from the buyer's.

import type { Line, Mandate, PageEvidence, PaymentConfirmation } from '../api'

export type DeskSummary = {
  dispute_id: string
  source: 'import' | 'webhook' | 'simulated'
  source_verified: boolean
  status: string | null
  reason: string | null
  reason_label: string | null
  stage: string | null
  amount: string | null
  currency: string | null
  due_at: string | null
  receipt_id: string | null
  has_valid_receipt: boolean
  recommendation: string | null
  updated_at: string
}

export type Packet = {
  facts: {
    dispute_id: string
    reason: string
    reason_label: string
    stage: string
    status: string
    disputed_amount: string | null
    payment_amount: string | null
    currency: string | null
    due_at: string | null
    days_left: number | null
    capture_id: string | null
    invoice_number: string | null
    receipt_id: string | null
    buyer: string | null
    items: { sku: string; name: string; qty: string }[]
  }
  buyer_messages: string[]
  requested_by_paypal: string[]
  authorization: {
    receipt_id: string
    user_text: string
    lines: Line[]
    total: string
    currency: string
    approved_by_human: boolean
    approved_by: string
    issued_at: string
    intent_id: string
    verify_url: string
    key_id: string
    paypal_order_id?: string | null
    intent_terms?: Mandate | null
    intent_approved_at?: string | null
    gateway_checks?: { rules: { name: string; passed: boolean }[]; injection_score?: number | null; decision?: string } | null
    semantic?: { match: { matches: number; partial: number; mismatch: number }; model: string; rationale?: string } | null
    ap2?: { present: boolean; valid: boolean | null; checks: { name: string; ok: boolean }[]; spec?: string; not_implemented?: string[]; has_payment_receipt?: boolean } | null
    page_evidence?: PageEvidence | null
    payment_confirmation?: PaymentConfirmation | null
    merchant_signature?: { present: boolean; valid: boolean | null; key_id?: string; detail?: string; matches_published_key?: boolean | null } | null
    witness?: { anchored: boolean; valid: boolean | null; gen_time?: string; tsa_name?: string; detail?: string } | null
    intent_user_signed?: boolean
    cart_user_signed?: boolean
  } | null
  checks: { name: string; ok: boolean; detail?: string }[]
  evidence: { kind: string; label: string; have: boolean; detail?: string | null }[]
  recommendation: { action: string; paypal_call: string | null; strength: string; why: string[] }
  economics: {
    avg_total_cost_usd: number
    avg_internal_labour_usd: number
    source: string
    disputed_amount: string | null
    small_claim_threshold_usd: string
  }
  narrative: string
  available_actions: string[]
  evidence_types: string[]
}

export type DeskDispute = DeskSummary & {
  packet: Packet
  fulfillment: { carrier: string; tracking_number: string } | null
  actions: { kind: string; by: string; ok: boolean; at: string; request: unknown; response: unknown }[]
}

// Seller sign-in lives in an HttpOnly cookie (path /desk) set by the desk: scripts never see it.
let sellerAuthed: boolean | null = null

export function getSellerAuth(): boolean | null {
  return sellerAuthed
}

export function setSellerAuth(v: boolean) {
  if (v === sellerAuthed) return
  sellerAuthed = v
  window.dispatchEvent(new Event('desk-auth'))
}

try {
  localStorage.removeItem('desk.token') // tokens from before Nov 2 are no longer used
} catch {
  /* storage unavailable */
}

// Same fallback as the buyer app: this tab's copy of the token if the cookie does not work.
const TAB_KEY = 'desk.tab-session'
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
    /* storage unavailable */
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  // The extra header proves the request comes from this app (blocks cross-site requests).
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Guardrail-Request': '1' }
  if (tabToken && path !== '/login') headers.Authorization = `Bearer ${tabToken}`
  const res = await fetch(`/desk/api${path}`, {
    method, headers, credentials: 'same-origin', body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 && path !== '/login') {
    setTabToken(null)
    setSellerAuth(false)
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* not JSON */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export async function checkSellerSession() {
  try {
    await call('GET', '/session')
    setSellerAuth(true)
  } catch {
    setSellerAuth(false)
  }
}

export const desk = {
  login: async (password: string) => {
    const r = await call<{ user: string; token: string }>('POST', '/login', { password })
    setTabToken(null)
    const viaCookie = await fetch('/desk/api/session', { credentials: 'same-origin' })
    if (!viaCookie.ok) setTabToken(r.token)
    return r
  },
  logout: async () => {
    try {
      return await call<{ ok: boolean }>('POST', '/logout')
    } finally {
      setTabToken(null)
    }
  },
  list: () => call<DeskSummary[]>('GET', '/disputes'),
  get: (id: string) => call<DeskDispute>('GET', `/disputes/${id}`),
  importCase: (dispute_id: string) => call<DeskDispute>('POST', '/disputes/import', { dispute_id }),
  refresh: (id: string) => call<DeskDispute>('POST', `/disputes/${id}/refresh`),
  fulfillment: (id: string, carrier: string, tracking_number: string) =>
    call<DeskDispute>('POST', `/disputes/${id}/fulfillment`, { carrier, tracking_number }),
  submit: (id: string, action: string, note: string, offer_amount?: string) =>
    call<DeskDispute>('POST', `/disputes/${id}/submit`, { action, note, offer_amount }),
}
