import { ApiProblem } from '../../shared/api'
import { opApi } from './session'

export const LIST_PATH = '/atendente/protocolos'
export const POLL_MS = 3000
/** Depois de entrar, volta para onde a guarda barrou; só aceita destinos da área do atendente. */
export const loginTarget = (from?: string) => (from?.startsWith(LIST_PATH) ? from : LIST_PATH)

export type HandoffStatus = 'aberto' | 'em_atendimento' | 'encerrado'
export type ListTab = 'abertos' | 'meus' | 'encerrados'
export interface OperatorRef { id: string; name: string }

export interface Ticket {
  protocol: string
  status: HandoffStatus
  reason: string
  summary: string
  conversation_id?: string | null
  user_id: string
  created_at: string
  updated_at?: string
  operator?: OperatorRef | null
  claimed_at?: string | null
  closed_at?: string | null
  resolution?: string | null
}
export interface TimelineEvent {
  kind: 'opened' | 'customer_message' | 'claimed' | 'operator_message' | 'note' | 'closed'
  at: string
  operator?: OperatorRef | null
  content?: string
  resolution?: string
  reason?: string
}
export interface DetailMessage { id: string; role: 'user' | 'assistant' | 'operator' | 'system'; content: string; author_name?: string; created_at?: string }
export interface Detail {
  handoff: Ticket & { timeline?: TimelineEvent[] }
  messages: DetailMessage[]
  customer: {
    user_id: string
    receivables: { sale_date: string; due_date: string; amount: string; status: string; payment_type: string }[]
    terminals: { model: string; connectivity: string; error?: string | null; serial_suffix: string }[]
  }
}

export const TABS: { id: ListTab; label: string; empty: string }[] = [
  { id: 'abertos', label: 'Abertos', empty: 'Nenhum protocolo aguardando atendimento. Novos protocolos aparecem aqui sozinhos.' },
  { id: 'meus', label: 'Meus', empty: 'Você não está atendendo nenhum protocolo agora. Assuma um na aba Abertos.' },
  { id: 'encerrados', label: 'Encerrados', empty: 'Nenhum protocolo encerrado ainda.' },
]

const REASONS: Record<string, string> = {
  cliente_pediu_humano: 'Cliente pediu atendente',
  sem_evidencia_suficiente: 'Sem informação suficiente',
  problema_nao_resolvido: 'Problema não resolvido',
  assunto_sensivel: 'Assunto sensível',
}
export const reasonLabel = (code: string) => REASONS[code] ?? code

const CODES: Record<string, string> = { credito_a_vista: 'Crédito à vista', credito_parcelado: 'Crédito parcelado', debito: 'Débito' }
/** Códigos dos dados do cliente em texto: "credito_a_vista" → "Crédito à vista", "previsto" → "Previsto". */
export const humanize = (code: string) => CODES[code] ?? (code.charAt(0).toUpperCase() + code.slice(1)).replaceAll('_', ' ')

export const STATUS_LABEL: Record<HandoffStatus, string> = { aberto: 'Aberto', em_atendimento: 'Em atendimento', encerrado: 'Encerrado' }

/** "3 min", "1 h 05 min", "2 dias 3 h": desde `from` até agora, ou até o encerramento. */
export function elapsed(t: Pick<Ticket, 'created_at' | 'closed_at'>, now?: number, from: string | null | undefined = t.created_at): string {
  const start = Date.parse(from ?? '')
  const end = t.closed_at ? Date.parse(t.closed_at) : now ?? Date.now()
  if (Number.isNaN(start) || Number.isNaN(end)) return 'não informado'
  const min = Math.max(0, Math.floor((end - start) / 60_000))
  if (min < 1) return 'menos de 1 min'
  if (min < 60) return `${min} min`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} h ${String(min % 60).padStart(2, '0')} min`
  const d = Math.floor(h / 24)
  return `${d} ${d === 1 ? 'dia' : 'dias'}${h % 24 ? ` ${h % 24} h` : ''}`
}

/** O que o atendente pode fazer no protocolo: assumir, atender (é o responsável) ou só ler. */
export function detailMode(t: Pick<Ticket, 'status' | 'operator'>, meId: string | undefined, conflictName?: string | null): 'claim' | 'owner' | 'readonly' {
  if (conflictName) return 'readonly'
  if (t.status === 'aberto') return 'claim'
  if (t.status === 'em_atendimento' && t.operator?.id === meId) return 'owner'
  return 'readonly'
}

/** Assume o protocolo; 409 handoff_already_claimed vira o nome de quem chegou antes. */
export async function claimHandoff(protocol: string): Promise<{ handoff: Ticket } | { conflict: string }> {
  try {
    return await opApi<{ handoff: Ticket }>(`/handoffs/${encodeURIComponent(protocol)}/claim`, { method: 'POST' })
  } catch (e) {
    if (e instanceof ApiProblem && e.status === 409 && e.code === 'handoff_already_claimed') {
      const name = (e.details as { operator_name?: string } | undefined)?.operator_name
      return { conflict: name || 'outro atendente' }
    }
    throw e
  }
}

export function author(m: DetailMessage): string {
  if (m.role === 'user') return 'Cliente'
  if (m.role === 'assistant') return 'Assistente'
  if (m.role === 'operator') return `Atendente ${m.author_name ?? ''}`.trim()
  return 'Aviso do sistema'
}

export const validateResolution = (text: string) => (text.trim() ? undefined : 'Escreva a nota de resolução para encerrar.')

const TIMELINE_TEXT: Record<TimelineEvent['kind'], string> = {
  opened: 'Protocolo aberto pelo assistente',
  customer_message: 'Cliente enviou mensagem',
  claimed: 'Assumiu a conversa',
  operator_message: 'Enviou mensagem ao cliente',
  note: 'Adicionou nota interna',
  closed: 'Encerrou o atendimento',
}
export const timelineText = (e: TimelineEvent) =>
  `${e.operator?.name ? `${e.operator.name}: ` : ''}${TIMELINE_TEXT[e.kind] ?? e.kind}${e.kind === 'opened' && e.reason ? ` (${reasonLabel(e.reason)})` : ''}`
