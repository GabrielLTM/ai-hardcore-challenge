import { useSyncExternalStore } from 'react'
import { api, ApiProblem, type ApiOptions } from '../../shared/api'

export interface OperatorSession { token: string; expires_at: string; operator: { id: string; name: string; email: string } }

const KEY = 'trilha.operator.session'
const listeners = new Set<() => void>()
let cache: { raw: string | null; value: OperatorSession | null } = { raw: null, value: null }
/** Motivo da última saída forçada; a tela de login mostra o aviso uma vez. */
let expiredFlag = false
// sessionStorage pode estar bloqueado: a sessão fica só na memória desta aba
let memoryRaw: string | null = null

function readRaw(): string | null {
  try { return sessionStorage.getItem(KEY) } catch { return memoryRaw }
}

export function getSession(): OperatorSession | null {
  const raw = readRaw()
  if (raw !== cache.raw) {
    let value: OperatorSession | null = null
    try { value = raw ? (JSON.parse(raw) as OperatorSession) : null } catch { value = null }
    cache = { raw, value }
  }
  const s = cache.value
  return s && s.token && !(s.expires_at && Date.parse(s.expires_at) <= Date.now()) ? s : null
}

function emit() { listeners.forEach((l) => l()) }

export function saveSession(s: OperatorSession) {
  memoryRaw = JSON.stringify(s)
  try { sessionStorage.setItem(KEY, memoryRaw) } catch { /* fica na memória */ }
  expiredFlag = false
  emit()
}

export function clearSession(expired = false) {
  memoryRaw = null
  try { sessionStorage.removeItem(KEY) } catch { /* fica na memória */ }
  expiredFlag = expired
  emit()
}

export const sessionExpired = () => expiredFlag

export function useOperatorSession(): OperatorSession | null {
  return useSyncExternalStore((cb) => { listeners.add(cb); return () => listeners.delete(cb) }, getSession, getSession)
}

/** Cliente das rotas /operator/*: único lugar que envia o token do atendente. 401 encerra a sessão. */
export async function opApi<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  const s = getSession()
  if (!s) { clearSession(true); throw new ApiProblem(401, 'unauthorized', 'Sua sessão expirou.') }
  try {
    return await api<T>(`/operator${path}`, { ...opts, headers: { ...opts.headers, Authorization: `Bearer ${s.token}` } })
  } catch (e) {
    if (e instanceof ApiProblem && e.status === 401) clearSession(true)
    throw e
  }
}
