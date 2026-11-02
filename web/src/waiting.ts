// Watches what is waiting for the person (permissions to review, carts held) while the app is
// open: a count on the Inbox link and in the tab title, and a desktop notification for each new
// item if the person allowed notifications on this device.
import { useEffect, useRef, useState } from 'react'
import { api, type Waiting } from './api'
import { navigate } from './router'

const BASE_TITLE = 'Spend Guardrail'

export function notificationsSupported() {
  return typeof window !== 'undefined' && 'Notification' in window
}

export function notificationPermission(): NotificationPermission | 'unsupported' {
  return notificationsSupported() ? Notification.permission : 'unsupported'
}

export async function enableNotifications(): Promise<NotificationPermission | 'unsupported'> {
  if (!notificationsSupported()) return 'unsupported'
  const p = await Notification.requestPermission()
  if (p === 'granted') {
    new Notification('Notifications are on', {
      body: 'You will be told here when your agent needs your approval.', icon: '/favicon.svg',
    })
  }
  return p
}

export function useWaiting(enabled: boolean, intervalMs = 5000): Waiting[] {
  const [items, setItems] = useState<Waiting[]>([])
  const seen = useRef<Set<string> | null>(null)

  useEffect(() => {
    if (!enabled) {
      setItems([])
      seen.current = null
      document.title = BASE_TITLE
      return
    }
    let stop = false
    const tick = async () => {
      try {
        const now = await api.waiting()
        if (stop) return
        setItems(now)
        document.title = now.length ? `(${now.length}) ${BASE_TITLE}` : BASE_TITLE
        const fresh = seen.current ? now.filter((w) => !seen.current!.has(w.id)) : []
        seen.current = new Set(now.map((w) => w.id))
        if (notificationPermission() === 'granted') {
          for (const w of fresh) {
            const n = new Notification(w.title, { body: w.text, icon: '/favicon.svg', tag: w.id })
            n.onclick = () => {
              window.focus()
              navigate(w.path)
              n.close()
            }
          }
        }
      } catch {
        /* signed out or offline: the next tick tries again */
      }
    }
    tick()
    const t = setInterval(tick, intervalMs)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [enabled, intervalMs])

  return items
}
