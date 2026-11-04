import { Link, useParams } from 'react-router-dom'
import { api } from '../../shared/api'
import { fmtDate, fmtDateTime } from '../../shared/format'
import { useDocumentTitle, useResource } from '../../shared/hooks'
import { Icon } from '../../shared/icons'
import { Button, Chip, CopyButton, ExternalLink, Note, ProblemPanel, StatePanel } from '../../shared/ui'
import { MenuButton } from '../../app/Shell'
import { kindLabel, phaseLabel, sourceBadge, type Source } from './model'
import { useStartIngestion } from './useStartIngestion'
import './knowledge.css'

export default function SourcePage() {
  const { sourceId = '' } = useParams()
  const res = useResource(`source:${sourceId}`, (signal) => api<Source>(`/knowledge/sources/${encodeURIComponent(sourceId)}`, { signal }))
  const ingestion = useStartIngestion()
  const s = res.data
  useDocumentTitle(s?.title ?? 'Fonte')
  const k = kindLabel(s?.document_type)
  const b = s ? sourceBadge(s) : null
  const attempt = s?.last_attempt
  const chunks = s?.chunks ?? []
  const versions = [...(s?.versions ?? [])].reverse()

  return (
    <main id="main" tabIndex={-1} className="page" aria-labelledby="fonte-titulo">
      <nav aria-label="Trilha de navegação" className="crumbs">
        <ol><li><Link to="/base">Base de conhecimento</Link></li><li aria-hidden="true">/</li><li aria-current="page">{s?.title ?? sourceId}</li></ol>
      </nav>
      {res.problem && !s ? (
        res.problem.status === 404
          ? <StatePanel kind="empty" title="Fonte não encontrada" message="Ela pode ter saído do catálogo." actions={<Link className="btn" to="/base">Voltar para a base</Link>} />
          : <ProblemPanel problem={res.problem} what="a fonte" onRetry={res.reload} />
      ) : !s ? <StatePanel kind="loading" what="a fonte" /> : (
        <>
          <div className="page__head">
            <div className="head-title" style={{ alignItems: 'flex-start' }}>
              <MenuButton />
              <div>
                <h1 id="fonte-titulo">{s.title}</h1>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                  <Chip icon={k.icon}>{k.label}</Chip>
                  {b && <Chip tone={b.tone} icon={b.icon}>{b.label}</Chip>}
                </div>
              </div>
            </div>
            <div className="page__actions">
              {s.source_url && <ExternalLink href={s.source_url} className="btn">Abrir original</ExternalLink>}
              <Button icon="refresh" busy={ingestion.busy} onClick={() => void ingestion.start([s.id])}>Reprocessar</Button>
            </div>
          </div>
          {ingestion.problem && <Note tone="danger" icon="alert">{ingestion.problem.message}{ingestion.problem.requestId && <> request_id <code>{ingestion.problem.requestId}</code></>}</Note>}
          {ingestion.started && <Note icon="check">Ingestão iniciada. <Link to="/base">Acompanhar as etapas na Base de conhecimento</Link>.</Note>}
          {attempt?.outcome === 'failed' && attempt.error && (
            <div role="alert" className="alert-box">
              <Icon name="alert" />
              <div><strong>A última tentativa falhou na etapa {phaseLabel(attempt.error.phase)}</strong><p style={{ margin: '4px 0 0' }}>{attempt.error.message ?? 'Falha sem detalhe informado.'} {s.status === 'stale' ? 'A versão indexada anteriormente foi mantida.' : 'A fonte ainda não foi indexada.'}</p></div>
            </div>
          )}

          <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', alignItems: 'start' }}>
            <section className="panel" aria-labelledby="meta-t">
              <div className="panel__head"><h2 id="meta-t">Metadados</h2></div>
              <dl className="kv panel__body" style={{ margin: 0 }}>
                <dt>URL</dt><dd>{s.source_url ? <ExternalLink href={s.source_url}>{s.source_url}</ExternalLink> : '—'}</dd>
                <dt>Publicador</dt><dd>{s.publisher ?? '—'}</dd>
                <dt>Coletada em</dt><dd>{fmtDate(s.retrieved_on) || '—'}</dd>
                <dt>Idioma/Região</dt><dd>{[s.language, s.region].filter(Boolean).join(' · ') || '—'}</dd>
                <dt>ID</dt><dd><code>{s.id}</code></dd>
                <dt>Hash</dt><dd>{s.content_sha256 ? <><code title={s.content_sha256}>{s.content_sha256.slice(0, 8)}…{s.content_sha256.slice(-5)}</code> <CopyButton text={s.content_sha256} label="Copiar hash completo" /></> : '—'}</dd>
                <dt>Processamento</dt><dd><code>{[s.processing_version, s.embedding_model, s.index_version?.slice(0, 12)].filter(Boolean).join(' · ') || 'não informado'}</code></dd>
                <dt>Última indexação</dt><dd>{s.last_indexed_at ? fmtDateTime(s.last_indexed_at) : 'Nunca indexada'}</dd>
              </dl>
            </section>
            <section className="panel" aria-labelledby="lim-t">
              <div className="panel__head"><h2 id="lim-t" style={{ display: 'flex', gap: 8, alignItems: 'center' }}><Icon name="alert" /> Limitações</h2></div>
              <div className="panel__body">
                {(s.limitations ?? []).length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma limitação registrada para esta fonte.</p> : <ul style={{ margin: 0, paddingLeft: 20 }}>{(s.limitations ?? []).map((l, i) => <li key={i}>{l}</li>)}</ul>}
                <p className="small muted" style={{ margin: 0 }}>Anotações editoriais da preparação da base, não declarações da Getnet.</p>
              </div>
            </section>
          </div>

          <section aria-labelledby="chunks-t" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <h2 id="chunks-t" style={{ margin: 0, fontSize: 16 }}>Trechos indexados ({chunks.length})</h2>
            {chunks.length === 0 && <StatePanel kind="empty" title="Nenhum trecho indexado" message="A fonte ainda não gerou trechos. Reprocesse para tentar de novo." />}
            {chunks.map((c) => (
              <article key={c.chunk_id} className="chunk" aria-label={c.heading ?? c.chunk_id}>
                <header style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}><code>{c.chunk_id}</code>{c.heading && <h3 style={{ margin: 0, fontSize: 14 }}>{c.heading}</h3>}</div>
                  <span className="small muted">{c.text.length.toLocaleString('pt-BR')} caracteres{s.embedding_model ? ` · ${s.embedding_model}` : ''}</span>
                </header>
                <p style={{ margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{c.text}</p>
              </article>
            ))}
          </section>

          <section className="panel" aria-labelledby="hist-t">
            <div className="panel__head"><h2 id="hist-t">Histórico de ingestão</h2></div>
            <div className="panel__body">
              {versions.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma indexação registrada.</p> : (
                <ol style={{ margin: 0, paddingLeft: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {versions.map((v, i) => (
                    <li key={`${v.index_version}-${v.indexed_at}`} style={{ display: 'flex', gap: '4px 12px', flexWrap: 'wrap', alignItems: 'center' }}>
                      <code>{v.index_version?.slice(0, 12) ?? 'sem versão'}</code>
                      <span>{fmtDateTime(v.indexed_at)}</span>
                      <span className="muted">{v.chunk_count ?? '?'} trechos · coletada em {fmtDate(v.retrieved_on) || '—'}</span>
                      <Chip tone={i === 0 ? 'success' : 'neutral'} icon={i === 0 ? 'check' : 'clock'}>{i === 0 ? 'Atual' : 'Anterior'}</Chip>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </section>
          <Note icon="lock">O conteúdo da base não pode ser editado pelo chat.</Note>
        </>
      )}
    </main>
  )
}
