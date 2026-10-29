import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export const cn = (...v: ClassValue[]) => twMerge(clsx(v))

export const pct = (n: number | null | undefined, N: number | null | undefined) =>
  n == null || !N ? null : (100 * n) / N

export const fmtPct = (p: number | null) => (p == null ? '' : p === 0 ? '0' : p < 0.05 ? '<0.1' : p.toFixed(1))

export const ago = (ts: number) => {
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 45) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return new Date(ts * 1000).toLocaleDateString()
}

export const clock = (ts?: number) => (ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '')

export const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
export const mod = isMac ? '⌘' : 'Ctrl'

export const cellKey = (c: { level: string; soc?: string | null; pt?: string | null; group: string }) =>
  [c.level, c.level === 'any' ? '' : c.soc ?? '', c.level === 'pt' ? c.pt ?? '' : '', c.group].join('␟')
