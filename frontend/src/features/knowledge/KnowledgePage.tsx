import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, readPage } from '../../shared/api'
import { fmtDate, fmtDateTime } from '../../shared/format'
import { useDocumentTitle, useResource } from '../../shared/hooks'
import { Icon } from '../../shared/icons'
import { Button, Chip, ExternalLink, ProblemPanel, StatePanel } from '../../shared/ui'
import { MenuButton } from '../../app/Shell'
import { IngestionPanel } from './Ingestion'
import { useStartIngestion } from './useStartIngestion'
import { jobActive, kindLabel, sourceBadge, type Job, type Source } from './model'
import './knowledge.css'

export default function KnowledgePage() {
  useDocumentTitle('Base de conhecimento')
  const sources = useResource('sources', async (signal) => readPage<Source>(await api('/knowledge/sources', { signal })).items)
  const jobs = useResource('jobs', async (signal) => readPage<Job>(await api('/knowledge/ingestions?limit=5', { signal })).items)
  const [trackedId, setTrackedId] = useState<string | null>(null)
  const jobId = trackedId ?? jobs.data?.[0]?.id ?? null
  const job = useResource(jobId ? `job:${jobId}` : null, (signal) => api<Job>(`/knowledge/ingestions/${jobId}`, { signal }))
  const [selected, setSelected] = useState<string[]>([])
  const ingestion = useStartIngestion((j) => { setTrackedId(j.id); jobs.reload(); setSelected([]) })
  const live = jobActive(job.data)
  const { reload: reloadJob } = job
  const { reload: reloadSources } = sources

  // Polling só enquanto há ingestão ativa; ao terminar, uma última leitura da lista mostra o estado final.
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => { reloadJob(); reloadSources() }, 2000)
    return () => { clearInterval(t); reloadSources() }
  }, [live, reloadJob, reloadSources])

  const rows = sources.data ?? []
  const lastIndexed = rows.map((s) => s.last_indexed_at).filter(Boolean).sort().at(-1)
  const models = [...new Set(rows.map((s) => s.embedding_model).filter(Boolean))]
  const versions = [...new Set(rows.map((s) => s.index_version?.slice(0, 8)).filter(Boolean))]
  const allSelected = rows.length > 0 && selected.length === rows.length
  const toggle = (id: string, on: boolean) => setSelected((l) => (on ? [...l, id] : l.filter((x) => x !== id)))

  return (
    <main id="main" tabIndex={-1} className="page" aria-labelledby="base-titulo">
      <div className="page__head">
        <div className="head-title" style={{ alignItems: 'flex-start' }}>
          <MenuButton />
          <div>
            <h1 id="base-titulo">Base de conhecimento</h1>
            <p>
              {rows.length ? `${rows.length} fontes` : 'Fontes e trechos que respondem às conversas'}
              {models.length ? ` · ${models.join(', ')}` : ''}{versions.length ? ` · índice ${versions.length === 1 ? versions[0] : 'com versões mistas'}` : ''}
              {lastIndexed ? ` · última indexação ${fmtDateTime(lastIndexed)} BRT` : ''}
            </p>
          </div>
        </div>
        <div className="page__actions">
          <Button icon="refresh" disabled={selected.length === 0 || live} busy={ingestion.busy} onClick={() => void ingestion.start(selected)}>Reprocessar selecionadas{selected.length ? ` (${selected.length})` : ''}</Button>
          <Button icon="refresh" disabled={rows.length === 0 || live} busy={ingestion.busy} onClick={() => void ingestion.start()}>Reprocessar todas</Button>
        </div>
      </div>

      {ingestion.problem && (
        <div role="alert" className="alert-box" style={{ alignItems: 'center' }}>
          <Icon name="alert" />
          <span>{ingestion.problem.message}{ingestion.problem.requestId && <> request_id <code>{ingestion.problem.requestId}</code></>}</span>
          <Button small variant="link" onClick={ingestion.clear}>Dispensar</Button>
        </div>
      )}
      {job.data && <IngestionPanel job={job.data} retrying={ingestion.busy} onRetry={(ids) => void ingestion.start(ids)} />}
      {job.problem && !job.data && <ProblemPanel problem={job.problem} what="a ingestão" onRetry={job.reload} />}

      <section className="panel" aria-labelledby="fontes-t">
        <div className="panel__head">
          <h2 id="fontes-t">Fontes</h2>
          <span aria-live="polite" className="small muted">{selected.length} de {rows.length} selecionadas</span>
        </div>
        {sources.problem && !sources.data ? <div className="panel__body"><ProblemPanel problem={sources.problem} what="as fontes" onRetry={sources.reload} /></div>
          : !sources.data ? <div className="panel__body"><StatePanel kind="loading" what="as fontes" /></div>
          : rows.length === 0 ? <div className="panel__body"><StatePanel kind="empty" title="Nenhuma fonte registrada" message="O catálogo é registrado pelo worker de ingestão. Inicie o worker e atualize esta página." actions={<Button icon="refresh" onClick={sources.reload}>Atualizar</Button>} /></div>
          : (
            <div className="table-wrap">
              <table className="table">
                <caption className="sr-only">Fontes da base de conhecimento: {rows.length} fontes com tipo, URL, data de coleta, status e última indexação</caption>
                <thead><tr>
                  <th scope="col"><input type="checkbox" id="sel-all" checked={allSelected} onChange={(e) => setSelected(e.target.checked ? rows.map((r) => r.id) : [])} /><label htmlFor="sel-all" className="sr-only">Selecionar todas</label></th>
                  <th scope="col">Título</th><th scope="col">Tipo</th><th scope="col">URL</th><th scope="col">Coletada em</th><th scope="col">Status</th><th scope="col">Última indexação</th><th scope="col">Ação</th>
                </tr></thead>
                <tbody>
                  {rows.map((s) => {
                    const k = kindLabel(s.document_type)
                    const b = sourceBadge(s)
                    return (
                      <tr key={s.id}>
                        <td><input type="checkbox" id={`s-${s.id}`} checked={selected.includes(s.id)} onChange={(e) => toggle(s.id, e.target.checked)} /><label htmlFor={`s-${s.id}`} className="sr-only">Selecionar {s.title}</label></td>
                        <td className="title">{s.title}</td>
                        <td><Chip icon={k.icon}>{k.label}</Chip></td>
                        <td style={{ maxWidth: 240 }}>{s.source_url ? <span title={s.source_url} className="clip"><ExternalLink href={s.source_url}><span className="clip">{s.source_url}</span></ExternalLink></span> : '—'}</td>
                        <td>{fmtDate(s.retrieved_on) || '—'}</td>
                        <td>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}>
                            <Chip tone={b.tone} icon={b.icon}>{b.label}</Chip>
                            {b.note && <span className="small muted"><span className="sr-only">· </span>{b.note}</span>}
                          </div>
                        </td>
                        <td>{s.last_indexed_at ? fmtDateTime(s.last_indexed_at) : 'Nunca indexada'}</td>
                        <td><Link to={`/base/${encodeURIComponent(s.id)}`} aria-label={`Abrir ${s.title}`}>Abrir</Link></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
      </section>
      <p className="muted small" style={{ margin: 0 }}>O conteúdo da base não pode ser editado pelo chat.</p>
    </main>
  )
}
