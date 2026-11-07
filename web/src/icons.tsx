// One icon per domain object and UI action, drawn on a 16px grid (stroke, no fill).
const PATHS: Record<string, string> = {
  inbox: 'M2 9.5h3.2l1 1.8h3.6l1-1.8H14M2.5 9.5 4.2 3.6a1 1 0 0 1 1-.6h5.6a1 1 0 0 1 1 .6l1.7 5.9V13a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1V9.5Z',
  cart: 'M1.5 2h2l1.6 8.2a1 1 0 0 0 1 .8h6.3a1 1 0 0 0 1-.8L14.5 5H4.2M6.5 14a.6.6 0 1 0 0-.01M12 14a.6.6 0 1 0 0-.01',
  permission: 'M8 1.8 2.8 3.9v3.6c0 3.1 2.2 5.6 5.2 6.7 3-1.1 5.2-3.6 5.2-6.7V3.9L8 1.8ZM5.8 8l1.6 1.6L10.4 6.4',
  agent: 'M5 5.5V4a3 3 0 0 1 6 0v1.5M3.5 5.5h9a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1V6.5a1 1 0 0 1 1-1ZM6 9h.01M10 9h.01M6.5 11h3',
  log: 'M5 3.5h8M5 8h8M5 12.5h8M2.5 3.5h.01M2.5 8h.01M2.5 12.5h.01',
  key: 'M10.2 9.6a3.5 3.5 0 1 0-3.4-2.7L2 11.7V14h2.3v-1.5h1.5V11h1.5l1.2-1.2c.5.2 1.1.3 1.7.3ZM11 5h.01',
  search: 'M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10ZM14 14l-3.5-3.5',
  close: 'M4 4l8 8M12 4l-8 8',
  expand: 'M9.5 2.5H13.5V6.5M13.5 2.5 8.5 7.5M6.5 13.5H2.5V9.5M2.5 13.5l5-5',
  lock: 'M4 7h8a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1ZM5.5 7V5a2.5 2.5 0 0 1 5 0v2',
  unlock: 'M4 7h8a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1ZM5.5 7V5a2.5 2.5 0 0 1 4.9-.7',
  check: 'M3 8.5l3 3 7-7',
  x: 'M4.5 4.5l7 7M11.5 4.5l-7 7',
  clock: 'M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12ZM8 4.8V8l2.2 1.4',
  store: 'M2.5 6.5V13a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1V6.5M1.8 6.2 3 2.5h10l1.2 3.7a1.7 1.7 0 0 1-3.2.9 1.8 1.8 0 0 1-3 0 1.8 1.8 0 0 1-3 0 1.7 1.7 0 0 1-3.2-.9ZM6.5 14v-3.5h3V14',
  seller: 'M8 2v12M4 14h8M3 5.5h10M3 5.5 1.5 9.5a1.6 1.6 0 0 0 3 0L3 5.5ZM13 5.5l-1.5 4a1.6 1.6 0 0 0 3 0L13 5.5ZM8 2l-5 3.5M8 2l5 3.5',
  menu: 'M2.5 4h11M2.5 8h11M2.5 12h11',
  bell: 'M4 11V7a4 4 0 0 1 8 0v4l1.2 1.5H2.8L4 11ZM6.6 14a1.5 1.5 0 0 0 2.8 0',
  logout: 'M6 2.5H3.5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1H6M10.5 11l3-3-3-3M13.5 8H6',
  fingerprint: 'M5 13.5c.9-1.4 1.4-3.2 1.4-5.2a1.6 1.6 0 0 1 3.2 0c0 1.3-.1 2.6-.4 3.8M3.2 11.6c.5-1.1.7-2.2.7-3.3a4.1 4.1 0 0 1 7.9-1.6M12.4 9.3c-.1 1.3-.4 2.6-.8 3.7M8 8.3c0 2.3-.6 4.3-1.6 5.9M2.5 6a5.9 5.9 0 0 1 10.6-1',
  bolt: 'M9 1.5 3.5 9H8l-1 5.5L12.5 7H8L9 1.5Z',
  page: 'M4 1.8h5l3 3V14a.4.4 0 0 1-.4.4H4a.4.4 0 0 1-.4-.4V2.2a.4.4 0 0 1 .4-.4ZM9 1.8v3h3M5.5 8.5h5M5.5 11h5',
  receipt: 'M3.5 1.8h9v12.4l-1.5-1-1.5 1-1.5-1-1.5 1-1.5-1-1.5 1V1.8ZM5.5 5h5M5.5 7.5h5M5.5 10h3',
  external: 'M9.5 2.5h4v4M13.5 2.5 7.5 8.5M11.5 9.5v3.5a.5.5 0 0 1-.5.5H3a.5.5 0 0 1-.5-.5V5a.5.5 0 0 1 .5-.5h3.5',
}

export type IconName = keyof typeof PATHS

export function Icon({ name, label }: { name: IconName | string; label?: string }) {
  return (
    <svg className="ic" viewBox="0 0 16 16" aria-hidden={label ? undefined : true} role={label ? 'img' : undefined} aria-label={label}>
      <path d={PATHS[name] ?? PATHS.page} />
    </svg>
  )
}
