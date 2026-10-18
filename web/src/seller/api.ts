// Client for the seller-side dispute desk (a separate server, proxied at /desk).
// Its login token is separate from the buyer's.

import type { Line } from '../api'

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

const TOKEN_KEY = 'desk.token'

export function getSellerToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setSellerToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable */
  }
  window.dispatchEvent(new Event('desk-auth'))
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getSellerToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(`/desk/api${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined })
  if (res.status === 401 && path !== '/login') setSellerToken(null)
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

export const desk = {
  login: (password: string) => call<{ token: string }>('POST', '/login', { password }),
  list: () => call<DeskSummary[]>('GET', '/disputes'),
  get: (id: string) => call<DeskDispute>('GET', `/disputes/${id}`),
  importCase: (dispute_id: string) => call<DeskDispute>('POST', '/disputes/import', { dispute_id }),
  refresh: (id: string) => call<DeskDispute>('POST', `/disputes/${id}/refresh`),
  fulfillment: (id: string, carrier: string, tracking_number: string) =>
    call<DeskDispute>('POST', `/disputes/${id}/fulfillment`, { carrier, tracking_number }),
  submit: (id: string, action: string, note: string, offer_amount?: string) =>
    call<DeskDispute>('POST', `/disputes/${id}/submit`, { action, note, offer_amount }),
}
