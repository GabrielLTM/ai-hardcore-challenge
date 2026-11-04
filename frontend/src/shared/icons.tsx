import type { ReactNode } from 'react'

/** Ícones de traço 16×16 da referência. Sempre decorativos: o nome acessível vem do texto vizinho. */
const P = (d: string) => <path d={d} />
const ICONS = {
  check: P('M3.5 8.5l3 3 6-7'),
  x: P('M4 4l8 8M12 4l-8 8'),
  plus: P('M8 3v10M3 8h10'),
  minus: P('M3.5 8h9'),
  stop: <rect x="4" y="4" width="8" height="8" rx="1" />,
  spinner: P('M8 2a6 6 0 1 0 6 6'),
  alert: P('M8 2.5l6 10.5H2z M8 6.5v3 M8 11.5v.01'),
  info: <><circle cx="8" cy="8" r="6" />{P('M8 7.5v3.5M8 5v.01')}</>,
  external: P('M9.5 2.5h4v4M13.5 2.5L7.5 8.5M12 9.5v4h-9.5v-9.5h4'),
  search: <><circle cx="7" cy="7" r="4.5" />{P('M10.5 10.5l3 3')}</>,
  refresh: P('M13.5 3v3.5H10M13.2 6.5A5.5 5.5 0 1 0 13 10'),
  clock: <><circle cx="8" cy="8" r="6" />{P('M8 5v3.2l2 1.3')}</>,
  chevronDown: P('M4 6l4 4 4-4'),
  chevronRight: P('M6 4l4 4-4 4'),
  chevronLeft: P('M10 4L6 8l4 4'),
  arrowDown: P('M8 3v10M4 9l4 4 4-4'),
  copy: <><rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />{P('M10.5 5.5V3.5a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2')}</>,
  chat: P('M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z'),
  agents: <><circle cx="4" cy="4" r="1.75" /><circle cx="12" cy="4" r="1.75" /><circle cx="8" cy="12" r="1.75" />{P('M5.3 5.2l1.8 5M10.7 5.2l-1.8 5M5.75 4h4.5')}</>,
  doc: P('M4 2.5h5.5l3 3v8H4z M9.5 2.5v3h3'),
  docCheck: P('M4 2.5h5.5l3 3v8H4z M9.5 2.5v3h3 M6.2 9.6l1.4 1.4 2.4-2.8'),
  globe: <><circle cx="8" cy="8" r="6" />{P('M2 8h12M8 2c2.2 2 2.2 10 0 12M8 2c-2.2 2-2.2 10 0 12')}</>,
  chart: P('M2.5 13.5h11M4 11V7M7 11V4M10 11V8M13 11V5.5'),
  wifiOff: P('M2 2l12 12M5.5 8.5a4 4 0 0 1 2.5-1M3 6a7.5 7.5 0 0 1 3-1.8M10.2 7.4A4 4 0 0 1 10.5 8.5M13 6a7.5 7.5 0 0 0-3.4-2M8 12v.01'),
  lock: <><rect x="3.5" y="7" width="9" height="6.5" rx="1" />{P('M5.5 7V5a2.5 2.5 0 0 1 5 0v2')}</>,
  menu: P('M2.5 4h11M2.5 8h11M2.5 12h11'),
  sidebar: <><rect x="2.5" y="3" width="11" height="10" rx="1.5" />{P('M6.5 3v10')}</>,
  circle: <circle cx="8" cy="8" r="5.5" />,
  pencil: P('M3 13l.7-3L10.5 3.2a1.4 1.4 0 0 1 2 2L5.7 12.3z'),
} satisfies Record<string, ReactNode>

export type IconName = keyof typeof ICONS

export function Icon({ name, size = 16, spin = false }: { name: IconName; size?: number; spin?: boolean }) {
  return (
    <svg aria-hidden="true" focusable="false" width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className={spin ? 'spin' : undefined} style={{ flex: 'none' }}>
      {ICONS[name]}
    </svg>
  )
}

export function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 32 32" style={{ flex: 'none' }}>
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M9 22c4 0 4-12 7-12s3 12 7 12" fill="none" stroke="#FFFFFF" strokeWidth="2.25" strokeLinecap="round" />
      <circle cx="9" cy="22" r="2.5" fill="#FFFFFF" />
      <circle cx="16" cy="10" r="2.5" fill="#FFFFFF" />
      <circle cx="23" cy="22" r="2.5" fill="#FFFFFF" />
    </svg>
  )
}
