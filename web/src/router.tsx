import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from 'react'

// Minimal history-API router: the gateway links to /intents/:id and /purchases/:id.

export function navigate(to: string) {
  window.history.pushState(null, '', to)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

/** Change the address without adding a history entry (filters, open drawer). */
export function replace(to: string) {
  window.history.replaceState(null, '', to)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function useLocation(): { path: string; query: URLSearchParams } {
  // Track path + query so /log -> /log?intent_id=... re-renders too.
  const [href, setHref] = useState(window.location.pathname + window.location.search)
  useEffect(() => {
    const on = () => setHref(window.location.pathname + window.location.search)
    window.addEventListener('popstate', on)
    return () => window.removeEventListener('popstate', on)
  }, [])
  const url = new URL(href, window.location.origin)
  return { path: url.pathname, query: url.searchParams }
}

export function match(pattern: string, path: string): Record<string, string> | null {
  const p = pattern.split('/').filter(Boolean)
  const a = path.split('/').filter(Boolean)
  if (p.length !== a.length) return null
  const params: Record<string, string> = {}
  for (let i = 0; i < p.length; i++) {
    if (p[i].startsWith(':')) params[p[i].slice(1)] = decodeURIComponent(a[i])
    else if (p[i] !== a[i]) return null
  }
  return params
}

export function Link({ href, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e)
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
    e.preventDefault()
    navigate(href)
  }
  return <a href={href} onClick={handle} {...rest} />
}
