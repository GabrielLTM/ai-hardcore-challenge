/** Datas da API chegam em UTC (RFC 3339); a interface exibe em America/Sao_Paulo (VITE_TIMEZONE). */
const TZ = (import.meta.env.VITE_TIMEZONE as string | undefined) ?? 'America/Sao_Paulo'
const TZ_LABEL = TZ === 'America/Sao_Paulo' ? 'BRT' : TZ

const fmt = (opts: Intl.DateTimeFormatOptions, timeZone = TZ) => new Intl.DateTimeFormat('pt-BR', { timeZone, ...opts })
const F = {
  time: fmt({ hour: '2-digit', minute: '2-digit' }),
  timeSec: fmt({ hour: '2-digit', minute: '2-digit', second: '2-digit' }),
  utcSec: fmt({ hour: '2-digit', minute: '2-digit', second: '2-digit' }, 'UTC'),
  date: fmt({ day: '2-digit', month: '2-digit', year: 'numeric' }),
  dayMonth: fmt({ day: '2-digit', month: '2-digit' }),
}

const valid = (iso?: string | null): Date | null => {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}

export const fmtTime = (iso?: string | null) => { const d = valid(iso); return d ? F.time.format(d) : '' }
// Datas sem horário (YYYY-MM-DD, ex.: retrieved_on) são dia de calendário: não sofrem deslocamento de fuso.
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/
export const fmtDate = (iso?: string | null) => {
  const m = iso ? DATE_ONLY.exec(iso) : null
  if (m) return `${m[3]}/${m[2]}/${m[1]}`
  const d = valid(iso)
  return d ? F.date.format(d) : ''
}
export const fmtDateTime = (iso?: string | null) => { const d = valid(iso); return d ? `${F.date.format(d)} ${F.time.format(d)}` : '' }
export const fmtDateTimeZone = (iso?: string | null) => { const d = valid(iso); return d ? `${F.date.format(d)} ${F.time.format(d)} ${TZ_LABEL}` : '' }
/** "14:31:52 BRT · 17:31:52 UTC" */
export const fmtBothZones = (iso?: string | null) => {
  const d = valid(iso)
  return d ? `${F.timeSec.format(d)} ${TZ_LABEL} · ${F.utcSec.format(d)} UTC` : 'não informado'
}

const dayKey = (d: Date) => F.date.format(d)
export function dayGroup(iso?: string | null, now = new Date()): string {
  const d = valid(iso)
  if (!d) return 'Sem data'
  if (dayKey(d) === dayKey(now)) return 'Hoje'
  if (dayKey(d) === dayKey(new Date(now.getTime() - 86_400_000))) return 'Ontem'
  return 'Anteriores'
}
/** Hoje mostra a hora; outros dias mostram dd/mm. */
export function listTime(iso?: string | null, now = new Date()): string {
  const d = valid(iso)
  if (!d) return ''
  return dayKey(d) === dayKey(now) ? F.time.format(d) : F.dayMonth.format(d)
}

export function fmtDuration(ms?: number | null): string {
  if (ms === undefined || ms === null || !Number.isFinite(ms) || ms < 0) return 'não informado'
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1).replace('.', ',')} s`
  const s = Math.round(ms / 1000)
  return `${Math.floor(s / 60)} min ${s % 60} s`
}

export const fmtInt = (n: number) => new Intl.NumberFormat('pt-BR').format(n)
export const fmtUsd = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'USD', minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(n)

export const plural = (n: number, one: string, many: string) => `${fmtInt(n)} ${n === 1 ? one : many}`
