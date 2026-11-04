/** Cliente HTTP da API /api/v1. Erros chegam como {code, message, request_id, details?}. */
export class ApiProblem extends Error {
  status: number
  code: string
  requestId?: string
  details?: unknown
  constructor(status: number, code: string, message: string, requestId?: string, details?: unknown) {
    super(message)
    this.name = 'ApiProblem'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.details = details
  }
}

export interface ApiOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  body?: unknown
  headers?: Record<string, string>
  signal?: AbortSignal
  timeoutMs?: number
}

const DEFAULT_MESSAGES: Record<number, string> = {
  401: 'Sua sessão expirou.',
  403: 'Você não tem acesso a este recurso.',
  404: 'Recurso não encontrado.',
  409: 'O recurso mudou desde que você o abriu.',
  410: 'Os dados solicitados expiraram.',
  412: 'A configuração mudou desde que você a abriu.',
}

/** details de 422 chega como [{loc, message}]: vira erro por campo. */
export const fieldErrors = (p: ApiProblem): Record<string, string> => {
  const out: Record<string, string> = {}
  if (Array.isArray(p.details)) for (const d of p.details as { loc?: string[]; message?: string }[]) { const k = d.loc?.[d.loc.length - 1]; if (k && d.message) out[k] = d.message }
  return out
}

export const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError'

export function toProblem(e: unknown): ApiProblem {
  if (e instanceof ApiProblem) return e
  return new ApiProblem(0, 'unknown', e instanceof Error ? e.message : 'Erro inesperado.')
}

export async function apiFull<T>(path: string, opts: ApiOptions = {}): Promise<{ data: T; headers: Headers }> {
  const timeout = AbortSignal.timeout(opts.timeoutMs ?? 30_000)
  const signal = opts.signal ? AbortSignal.any([opts.signal, timeout]) : timeout
  const headers: Record<string, string> = { Accept: 'application/json', ...opts.headers }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  let res: Response
  try {
    res = await fetch(`/api/v1${path}`, {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal,
    })
  } catch (e) {
    if (opts.signal?.aborted) throw e
    if (timeout.aborted) throw new ApiProblem(0, 'timeout', 'A solicitação demorou mais que o esperado.')
    throw new ApiProblem(0, 'network', 'Não foi possível falar com o servidor.')
  }
  if (!res.ok) {
    let body: Record<string, unknown> = {}
    try { body = await res.json() } catch { /* corpo vazio ou não JSON */ }
    const detail = typeof body.detail === 'string' ? body.detail : undefined
    throw new ApiProblem(
      res.status,
      typeof body.code === 'string' ? body.code : `http_${res.status}`,
      (typeof body.message === 'string' ? body.message : detail) ?? DEFAULT_MESSAGES[res.status] ?? `Erro ${res.status}.`,
      (typeof body.request_id === 'string' ? body.request_id : undefined) ?? res.headers.get('X-Request-ID') ?? undefined,
      body.details,
    )
  }
  const data = (res.status === 204 ? undefined : await res.json().catch(() => undefined)) as T
  return { data, headers: res.headers }
}

export const api = async <T,>(path: string, opts?: ApiOptions) => (await apiFull<T>(path, opts)).data

export const qs = (params: Record<string, string | number | undefined | null>) => {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
  const s = q.toString()
  return s ? `?${s}` : ''
}

/** Aceita {items,next_cursor}, {data,...} ou array puro. */
export function readPage<T>(raw: unknown): { items: T[]; next: string | null; total?: number } {
  if (Array.isArray(raw)) return { items: raw as T[], next: null }
  const r = (raw ?? {}) as Record<string, unknown>
  const items = (r.items ?? r.data ?? []) as T[]
  const next = (r.next_cursor ?? r.next ?? null) as string | null
  const total = typeof r.total === 'number' ? r.total : typeof r.count === 'number' ? r.count : undefined
  return { items: Array.isArray(items) ? items : [], next, total }
}
