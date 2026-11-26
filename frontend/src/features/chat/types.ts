export interface Conversation {
  id: string
  title: string
  updated_at?: string
  created_at?: string
  user_id?: string
  status?: string
  last_run_status?: string
  active_run_id?: string | null
}
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'operator' | 'system'
  content: string
  /** Nome do atendente, nas mensagens role=operator. */
  author_name?: string
  status?: string
  run_id?: string
  created_at?: string
  request_id?: string
  agent_name?: string
  error?: { code?: string; message?: string }
}
/** Protocolo ativo da conversa, como o cliente o vê. */
export interface CustomerHandoff { protocol: string; status: 'aberto' | 'em_atendimento' | 'encerrado'; operator_name?: string | null }
/** Em modo humano o servidor guarda a mensagem para o atendente: run_id null, status with_operator e nenhum SSE. */
export interface StartedRun { conversation_id?: string; run_id: string | null; status?: string; events_url?: string; handoff?: CustomerHandoff }

/** Rótulo do chip na lista: só aparece quando a conversa NÃO está concluída. */
export function conversationChip(c: Conversation, liveStatus?: string): { label: string; tone: 'progress' | 'neutral' | 'warning' | 'danger'; icon: 'spinner' | 'x' | 'alert' } | null {
  const s = liveStatus ?? c.last_run_status ?? c.status ?? (c.active_run_id ? 'running' : undefined)
  if (s === 'running' || s === 'queued') return { label: 'Gerando', tone: 'progress', icon: 'spinner' }
  if (s === 'cancelled') return { label: 'Cancelada', tone: 'neutral', icon: 'x' }
  if (s === 'interrupted') return { label: 'Interrompida', tone: 'warning', icon: 'alert' }
  if (s === 'failed') return { label: 'Falhou', tone: 'danger', icon: 'alert' }
  return null
}
