import { api, readPage } from '../../shared/api'
import { useResource } from '../../shared/hooks'
import type { AgentSummary } from './types'

/** Todos os agentes do workspace (até 100): nomes dos destinos e opções de delegação. */
export function useAllAgents() {
  const res = useResource('agents:all', async (signal) => readPage<AgentSummary>(await api('/agents?limit=100', { signal })).items)
  const names: Record<string, string> = Object.fromEntries((res.data ?? []).map((a) => [a.id, a.name]))
  return { ...res, agents: res.data ?? [], names }
}
