import { fmtDateTime } from '../../shared/format'
import { Icon } from '../../shared/icons'
import { Button, Chip, Note } from '../../shared/ui'
import { jobActive, PHASE_STATUS, PHASES, phaseLabel, type Job } from './model'

/** Acompanha uma ingestão por polling de GET /knowledge/ingestions/{id}; as fases são as do worker, sem percentual. */
export function IngestionPanel({ job, onRetry, retrying }: { job: Job; onRetry: (ids: string[]) => void; retrying: boolean }) {
  const running = jobActive(job)
  const failedIds = (job.results ?? []).filter((r) => r.outcome === 'failed').map((r) => r.source_id)
  const failedPhase = job.error?.phase ?? PHASES.find((p) => job.phases?.[p]?.status === 'failed')
  const updated = (job.results ?? []).filter((r) => r.outcome === 'updated').length
  const current = (job.results ?? []).filter((r) => r.outcome === 'already_current').length
  const badge = running ? (job.status === 'queued' ? { tone: 'neutral' as const, icon: 'clock' as const, label: 'Na fila' } : { tone: 'progress' as const, icon: 'spinner' as const, label: 'Em andamento' })
    : job.status === 'succeeded' ? { tone: 'success' as const, icon: 'check' as const, label: 'Concluída' }
    : job.status === 'partial' ? { tone: 'warning' as const, icon: 'alert' as const, label: 'Concluída com falhas' }
    : { tone: 'danger' as const, icon: 'alert' as const, label: 'Falhou' }
  return (
    <section className="panel" aria-labelledby="ing-t" aria-live="polite">
      <div className="panel__head">
        <div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <h2 id="ing-t">{running ? 'Ingestão em andamento' : 'Última ingestão'}</h2>
            <Chip tone={badge.tone} icon={badge.icon}>{badge.label}</Chip>
          </div>
          <p className="small muted" style={{ margin: '4px 0 0' }}>
            <code>{job.id}</code> · {job.started_at ? `iniciada ${fmtDateTime(job.started_at)}` : `criada ${fmtDateTime(job.created_at)}`}{job.requested_by ? ` por ${job.requested_by}` : ''}
            {job.attempts && job.attempts > 1 ? ` · tentativa ${job.attempts} de ${job.max_attempts ?? '?'}` : ''}
          </p>
        </div>
      </div>
      <div className="panel__body">
        <ol className="phases" aria-label="Etapas da ingestão">
          {PHASES.map((p, i) => {
            const st = job.phases?.[p] ?? { status: 'pending' }
            const meta = PHASE_STATUS[st.status] ?? PHASE_STATUS.pending
            const total = job.source_ids?.length
            return (
              <li key={p} className="phase" aria-current={st.status === 'running' ? 'step' : undefined}>
                <span className="small muted">Etapa {i + 1} de {PHASES.length}</span>
                <strong>{phaseLabel(p)}</strong>
                <Chip tone={meta.tone} icon={meta.icon}>{meta.label}</Chip>
                {st.processed !== undefined && <span className="small">{st.processed}{total ? ` de ${total}` : ''} {st.processed === 1 ? 'fonte' : 'fontes'}{st.failed ? ` · ${st.failed} com falha` : ''}</span>}
                {st.status === 'pending' && running && <span className="small muted">Aguarda a etapa anterior</span>}
              </li>
            )
          })}
        </ol>
        {running && <Note>O índice atual continua atendendo às conversas até a conclusão.</Note>}
        {!running && job.status === 'succeeded' && <Note icon="check">{updated} {updated === 1 ? 'fonte atualizada' : 'fontes atualizadas'}{current ? `, ${current} já ${current === 1 ? 'estava atualizada' : 'estavam atualizadas'}` : ''}.</Note>}
        {(job.status === 'failed' || job.status === 'partial') && (
          <div role="alert" className="alert-box" style={{ flexWrap: 'wrap' }}>
            <Icon name="alert" />
            <div style={{ flex: 1, minWidth: 220 }}>
              <strong>Ingestão <code>{job.id.slice(0, 8)}</code> {job.status === 'partial' ? 'terminou com falhas' : 'falhou'} na etapa {phaseLabel(failedPhase)}</strong>
              <p style={{ margin: '4px 0 0' }}>
                {job.error?.source_id && <><code>{job.error.source_id}</code>: </>}{job.error?.message ?? 'Falha sem detalhe informado.'} As versões indexadas anteriormente foram mantidas.
              </p>
              {(job.results ?? []).filter((r) => r.outcome === 'failed').length > 1 && <p className="small" style={{ margin: '4px 0 0' }}>{failedIds.length} fontes com falha: {failedIds.join(', ')}.</p>}
            </div>
            <Button icon="refresh" busy={retrying} onClick={() => onRetry(failedIds)}>Tentar novamente {failedIds.length === 1 ? 'a fonte com falha' : 'as fontes com falha'}</Button>
          </div>
        )}
      </div>
    </section>
  )
}
