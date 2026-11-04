import { kindOf } from '../chat/evidence'
import type { IconName } from '../../shared/icons'
import type { Tone } from '../../shared/ui'

export const PHASES = ['collect', 'extract', 'chunk', 'embed', 'index'] as const
export const PHASE_LABEL: Record<string, string> = { collect: 'Coletar', extract: 'Extrair', chunk: 'Dividir', embed: 'Embeddings', index: 'Indexar' }
export const phaseLabel = (p?: string | null) => (p && PHASE_LABEL[p]) || p || 'etapa desconhecida'

export interface SourceError { phase?: string; code?: string; message?: string; source_id?: string }
export interface Source {
  id: string
  title: string
  document_type?: string
  source_url?: string
  publisher?: string
  region?: string
  language?: string
  limitations?: string[]
  retrieved_on?: string
  content_sha256?: string
  processing_version?: string
  index_version?: string
  embedding_model?: string
  chunk_count?: number
  status?: string
  last_indexed_at?: string
  last_attempt?: { at?: string; outcome?: string; job_id?: string; error?: SourceError }
  versions?: { index_version?: string; content_sha256?: string; embedding_model?: string; chunk_count?: number; retrieved_on?: string; indexed_at?: string }[]
  chunks?: Chunk[]
}
export interface Chunk { chunk_id: string; chunk_index?: number; heading?: string; text: string; chunk_sha256?: string }

export interface PhaseState { status: string; processed?: number; failed?: number; started_at?: string; finished_at?: string }
export interface Job {
  id: string
  status: string
  source_ids: string[]
  phases: Record<string, PhaseState>
  results?: { source_id: string; outcome: string; chunks?: number; error?: SourceError }[]
  error?: SourceError | null
  requested_by?: string
  attempts?: number
  max_attempts?: number
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
}
export const jobActive = (j?: Job) => j?.status === 'queued' || j?.status === 'running'

export const kindLabel = (t?: string) => {
  const k = kindOf(t)
  return k === 'official' ? { label: 'Fonte oficial', icon: 'docCheck' as IconName } : k === 'web' ? { label: 'Web', icon: 'globe' as IconName } : { label: 'Curada', icon: 'doc' as IconName }
}

export interface Badge { tone: Tone; icon: IconName; label: string; note?: string }

/** Estado da fonte em linguagem de operação; a última versão válida nunca é apagada por uma falha. */
export function sourceBadge(s: Source): Badge {
  const err = s.last_attempt?.error
  if (s.status === 'stale' || s.status === 'failed') {
    const never = s.status === 'failed'
    if (err?.code === 'empty') return { tone: 'warning', icon: 'alert', label: 'Vazia', note: 'nenhum texto extraído' }
    return { tone: 'danger', icon: 'alert', label: `Falha em ${phaseLabel(err?.phase)}`, note: never ? 'nunca indexada' : 'versão anterior mantida' }
  }
  if (s.status === 'pending') return { tone: 'neutral', icon: 'circle', label: 'Pendente', note: 'aguarda a primeira indexação' }
  if (s.last_attempt?.outcome === 'already_current') return { tone: 'neutral', icon: 'check', label: 'Já atualizada', note: 'sem mudanças' }
  return { tone: 'success', icon: 'check', label: 'Indexada' }
}

export const PHASE_STATUS: Record<string, { tone: Tone; icon: IconName; label: string }> = {
  pending: { tone: 'neutral', icon: 'circle', label: 'Não iniciado' },
  running: { tone: 'progress', icon: 'spinner', label: 'Em andamento' },
  succeeded: { tone: 'success', icon: 'check', label: 'Sucesso' },
  partial: { tone: 'warning', icon: 'alert', label: 'Parcial' },
  failed: { tone: 'danger', icon: 'alert', label: 'Erro' },
  skipped: { tone: 'neutral', icon: 'minus', label: 'Nada a fazer' },
}
