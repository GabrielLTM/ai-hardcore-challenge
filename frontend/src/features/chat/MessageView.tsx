import { useToast } from '../../shared/toast-context'
import { Markdown } from '../../shared/markdown'
import { Button, Chip, CopyButton } from '../../shared/ui'
import { Icon } from '../../shared/icons'
import { fmtTime } from '../../shared/format'
import { PHASE_LABEL, PHASE_LIVE_TEXT, type Phase } from './runs'

export function UserBubble({ text, at }: { text: string; at?: string }) {
  return (
    <div className="bubble bubble--user">
      <span className="small muted">Você{at ? ` · ${fmtTime(at)}` : ''}</span>
      <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text}</div>
    </div>
  )
}

interface AssistantProps {
  phase: Phase
  text: string
  byline?: string
  at?: string
  runId?: string
  requestId?: string
  failureMessage?: string
  /** Texto ainda crescendo: mostra o cursor, mas sem anúncio por token. */
  streaming?: boolean
  actionsDisabled?: boolean
  onCite?: (runId: string, marker: string) => void
  onSources?: (runId: string) => void
  onResend?: () => void
  slowHint?: React.ReactNode
}

export function AssistantBubble({ phase, text, byline, at, runId, requestId, failureMessage, streaming, actionsDisabled, onCite, onSources, onResend, slowHint }: AssistantProps) {
  const toast = useToast()
  const active = phase === 'waiting' || phase === 'routing' || phase === 'running' || phase === 'generating'
  const partial = phase === 'cancelled' || phase === 'interrupted'
  const cite = runId && onCite ? (m: string) => onCite(runId, m) : undefined
  return (
    <article className="bubble bubble--assistant" aria-label="Resposta" data-phase={phase}>
      {byline && <div className="small muted">{byline}</div>}
      {partial && text && <div className="partial-label"><Icon name={phase === 'cancelled' ? 'stop' : 'alert'} size={14} />{phase === 'cancelled' ? 'Resposta parcial: interrompida por você' : 'Resposta parcial: execução interrompida'}</div>}
      {text
        ? <div aria-live="off"><Markdown text={text} onCite={cite} caret={streaming && phase === 'generating'} /></div>
        : null}
      {phase === 'failed' && (
        <div>
          <p role="alert" style={{ margin: '0 0 4px', color: 'var(--danger-fg)', fontWeight: 500 }}>Não foi possível concluir a resposta.</p>
          {failureMessage && !failureMessage.startsWith('Não foi possível concluir') && <p className="small muted" style={{ margin: '0 0 4px' }}>{failureMessage}</p>}
          {requestId && <p className="small" style={{ margin: 0 }}>request_id <code>{requestId}</code> <CopyButton text={requestId} label="Copiar request_id" /></p>}
        </div>
      )}
      {phase === 'interrupted' && <p className="small muted" style={{ margin: 0 }}>A execução parou porque o servidor responsável ficou indisponível. O texto recebido foi mantido.</p>}
      {slowHint}
      <div className="bubble__foot">
        <span className="bubble__status" aria-live="polite">
          {active && <><Chip tone="progress" icon={phase === 'waiting' ? 'clock' : 'spinner'} spin={phase !== 'waiting'}>{PHASE_LABEL[phase]}</Chip><span className="tone-progress">{PHASE_LIVE_TEXT[phase]}</span></>}
          {phase === 'cancelled' && <span className="muted"><Icon name="stop" size={14} />Parado por você · texto parcial</span>}
          {phase === 'completed' && <span className="tone-success"><Icon name="check" size={14} />{at ? `Concluída às ${fmtTime(at)}` : 'Concluída'}</span>}
        </span>
        {phase === 'completed' && text && (
          <Button small icon="copy" onClick={async () => { try { await navigator.clipboard.writeText(text); toast('Resposta copiada.') } catch { toast('Não foi possível copiar.', 'danger') } }}>Copiar</Button>
        )}
        {runId && onSources && !active && <Button small icon="doc" onClick={() => onSources(runId)}>Ver fontes</Button>}
        {onResend && phase === 'cancelled' && <Button small icon="refresh" disabled={actionsDisabled} onClick={onResend}>Enviar novamente</Button>}
        {onResend && phase === 'failed' && <Button small icon="refresh" disabled={actionsDisabled} onClick={onResend}>Tentar novamente</Button>}
        {onResend && phase === 'interrupted' && <Button small icon="refresh" disabled={actionsDisabled} onClick={onResend}>Iniciar nova execução</Button>}
      </div>
    </article>
  )
}
