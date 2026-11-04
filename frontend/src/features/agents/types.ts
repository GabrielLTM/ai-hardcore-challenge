/** Contrato de /api/v1/agents (admin). Campos opcionais porque o backend evolui em paralelo. */
export interface Limits { max_output_tokens?: number; max_tool_calls?: number }

export interface AgentContent {
  name: string
  description: string
  kind: string
  instructions: string
  model: string
  tools: string[]
  delegates: string[]
  limits: Limits
  enabled: boolean
}

export interface AgentSummary {
  id: string
  name: string
  description: string
  kind: string
  enabled: boolean
  tools: string[]
  delegates: string[]
  revision: number
  status: 'draft' | 'published'
  version: number | null
  has_unpublished_changes?: boolean
  updated_at?: string
}

export interface PublishedVersion extends AgentContent {
  version: number
  prompt_version?: number
  summary?: string
  created_by?: string
  created_at?: string
}

export interface AgentFull extends AgentSummary, AgentContent {
  prompt_id?: string
  created_at?: string
  updated_by?: string
  published: PublishedVersion | null
}

export interface VersionRow {
  version: number
  summary?: string
  created_by?: string
  created_at?: string
  is_current?: boolean
  restored_from?: number | null
  prompt_version?: number
  name?: string
}

export interface GraphIssue { code?: string; message?: string; agent_id?: string | null; path?: string[] }
export interface Validation { valid: boolean; graph_revision?: number | string | null; stale?: boolean; errors: GraphIssue[]; cycle?: string[] | null }

export interface Relationship { id: string; name: string | null; version: number | null; status: string; enabled?: boolean }
export interface Relationships { inbound: Relationship[]; outbound: Relationship[]; graph_version?: string | null }

export const KIND_LABEL: Record<string, string> = { router: 'Router', support: 'Suporte', knowledge: 'Conhecimento', custom: 'Personalizado' }
export const DELEGATION_TOOL = 'delegate_to_agent'

export const TOOL_CATALOG: { name: string; description: string }[] = [
  { name: 'search_knowledge', description: 'Busca híbrida na base curada e nas fontes oficiais coletadas.' },
  { name: 'web_search', description: 'Busca ao vivo na web, só para perguntas gerais e atuais.' },
  { name: 'get_receivables', description: 'Consulta recebíveis do cliente autorizado (dados de demonstração).' },
  { name: 'request_human_handoff', description: 'Registra o encaminhamento para um operador humano e devolve o protocolo (simulação de demonstração).' },
  { name: 'get_terminal_status', description: 'Consulta a situação dos terminais do cliente autorizado (dados de demonstração).' },
]

export const toForm = (a: AgentContent): AgentContent => ({
  name: a.name ?? '', description: a.description ?? '', kind: a.kind ?? 'custom', instructions: a.instructions ?? '',
  model: a.model ?? '', tools: (a.tools ?? []).filter((t) => t !== DELEGATION_TOOL), delegates: a.delegates ?? [],
  limits: a.limits ?? {}, enabled: a.enabled ?? true,
})

/** delegate_to_agent não é escolha do operador: existe quando há agentes delegáveis. */
export const toPayload = (f: AgentContent): AgentContent => ({
  ...f, tools: f.delegates.length > 0 ? [...f.tools, DELEGATION_TOOL] : f.tools,
})

export const sameContent = (a: AgentContent, b: AgentContent) =>
  JSON.stringify([a.name, a.description, a.kind, a.instructions, a.model, [...a.tools].sort(), [...a.delegates].sort(), a.limits, a.enabled]) ===
  JSON.stringify([b.name, b.description, b.kind, b.instructions, b.model, [...b.tools].sort(), [...b.delegates].sort(), b.limits, b.enabled])

export const fmtLimits = (l: Limits | undefined) => {
  const parts = [l?.max_tool_calls ? `${l.max_tool_calls} chamadas de tool` : '', l?.max_output_tokens ? `${l.max_output_tokens} tokens de saída` : ''].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'padrões do servidor'
}
