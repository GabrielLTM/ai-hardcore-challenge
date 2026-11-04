import { useEffect, useRef, type KeyboardEvent } from 'react'
import { Button, Chip, CopyButton, DemoChip, ExternalLink, ProblemPanel, StatePanel } from '../../shared/ui'
import { Icon } from '../../shared/icons'
import type { ApiProblem } from '../../shared/api'
import { fmtBothZones, fmtDate, fmtDuration, fmtInt, fmtUsd } from '../../shared/format'
import { matchesMarker, type Evidence, type EvidenceItem } from './evidence'
import { buildSteps, buildTech, phaseOf, type Step } from './runs'
import { grafanaTraceUrl } from '../../shared/grafana'
import type { RunStream } from './useRunStream'

export type Tab = 'andamento' | 'fontes'

export interface EvidenceState { data?: Evidence; loading: boolean; problem?: ApiProblem; reload: () => void }

interface Props {
  run: RunStream | null
  evidence: EvidenceState
  tab: Tab
  onTab: (t: Tab) => void
  onClose: () => void
  highlight: string | null
  /** Dentro de drawer/sheet o botão de fechar é o do próprio diálogo. */
  embedded?: boolean
}

const STEP_ICON: Record<Step['status'], { icon: 'check' | 'spinner' | 'x' | 'alert' | 'circle'; cls: string; sr: string }> = {
  done: { icon: 'check', cls: 'tone-success', sr: 'Concluído: ' },
  running: { icon: 'spinner', cls: 'tone-progress', sr: 'Em andamento: ' },
  error: { icon: 'alert', cls: 'tone-danger', sr: 'Com problema: ' },
  cancelled: { icon: 'x', cls: 'muted', sr: 'Interrompido: ' },
  pending: { icon: 'circle', cls: 'muted', sr: 'Aguardando: ' },
}

export function SidePanel({ run, evidence, tab, onTab, onClose, highlight, embedded }: Props) {
  const tabs = useRef<Record<Tab, HTMLButtonElement | null>>({ andamento: null, fontes: null })
  const count = evidence.data ? evidence.data.cited.length + evidence.data.notUsed.length : undefined
  const move = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft' && e.key !== 'Home' && e.key !== 'End') return
    e.preventDefault()
    const next: Tab = e.key === 'ArrowRight' || e.key === 'End' ? 'fontes' : 'andamento'
    onTab(next)
    tabs.current[next]?.focus()
  }
  return (
    <div className="sidepanel">
      <div className="sidepanel__bar">
        <div role="tablist" aria-label="Painel lateral" style={{ display: 'flex', gap: 4 }}>
          {(['andamento', 'fontes'] as const).map((t) => (
            <button key={t} ref={(el) => { tabs.current[t] = el }} type="button" role="tab" id={`tab-${t}`} className="tab"
              aria-selected={tab === t} aria-controls={`painel-${t}`} tabIndex={tab === t ? 0 : -1} onKeyDown={move} onClick={() => onTab(t)}>
              {t === 'andamento' ? 'Andamento' : `Fontes${count !== undefined ? ` (${count})` : ''}`}
            </button>
          ))}
        </div>
        {!embedded && <Button variant="link" className="btn--icon" aria-label="Recolher painel" onClick={onClose}><Icon name="chevronRight" /></Button>}
      </div>
      <div role="tabpanel" id={`painel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0} className="sidepanel__body">
        {!run || !run.runId
          ? <StatePanel kind="empty" title="Nada para mostrar ainda" message="Envie uma mensagem para acompanhar o andamento e as fontes da resposta." />
          : tab === 'andamento' ? <Andamento run={run} evidence={evidence.data} onTab={onTab} /> : <Fontes run={run} evidence={evidence} highlight={highlight} />}
      </div>
    </div>
  )
}

function Andamento({ run, evidence, onTab }: { run: RunStream; evidence?: Evidence; onTab: (t: Tab) => void }) {
  const { state, info } = run
  const phase = phaseOf(state, info?.status)
  const limitation = evidence?.cited.flatMap((c) => c.limitations)[0]
  const known = evidence && evidence.cited.length + evidence.notUsed.length > 0
  const steps = buildSteps(state, phase, { citedCount: known ? evidence.cited.length : undefined, limitation })
  const tech = buildTech(run.runId ?? '', state, info)
  const hasEvents = state.events.length > 0
  return (
    <>
      {run.reconnecting && (
        <div role="status" className="note note--warning" style={{ marginBottom: 12 }}>
          <Icon name="wifiOff" size={14} /><span>Conexão perdida. Tentando reconectar; o texto já recebido foi mantido. <Button variant="bare" onClick={run.retryNow}>Tentar agora</Button></span>
        </div>
      )}
      {run.expired && <div role="status" className="note" style={{ marginBottom: 12 }}><Icon name="clock" size={14} /><span>Eventos expirados (retenção de 7 dias). Mostrando o estado final da execução.</span></div>}
      {run.problem && <ProblemPanel problem={run.problem} what="o andamento" onRetry={run.retryNow} />}
      {!hasEvents && !run.expired && phase !== 'waiting' && !run.reconnecting && !run.problem && (
        <div role="status" className="note" style={{ marginBottom: 12 }}><Icon name="info" size={14} /><span>Sem eventos registrados para esta execução.</span></div>
      )}
      <ol className="steps" aria-label="Andamento do atendimento">
        {steps.map((s) => {
          const i = STEP_ICON[s.status]
          return (
            <li key={s.id} className="step" aria-live={s.status === 'running' ? 'polite' : undefined}>
              <span className={i.cls} style={{ marginTop: 3 }}><Icon name={i.icon} spin={i.icon === 'spinner'} /></span>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}><span className="sr-only">{i.sr}</span>{s.title}</div>
                {s.detail && <div className="small" style={{ fontSize: 13, color: 'var(--ink-2)' }}>{s.detail}</div>}
                {s.requestId && <div className="small">request_id <code>{s.requestId}</code></div>}
                {s.demo && <div style={{ marginTop: 4 }}><DemoChip /></div>}
                {s.sources && <button type="button" className="btn btn--bare small" style={{ marginTop: 2 }} onClick={() => onTab('fontes')}>Ver fontes</button>}
              </div>
            </li>
          )
        })}
      </ol>
      <details className="tech">
        <summary>Detalhes técnicos</summary>
        <dl className="kv" style={{ marginTop: 12, fontSize: 12, gridTemplateColumns: '88px minmax(0,1fr)' }}>
          <dt>Execução</dt>
          <dd><span className="mono" style={{ wordBreak: 'break-all' }}>{tech.runId}</span><div><CopyButton text={tech.runId} label="Copiar ID da execução" /></div></dd>
          <dt>Início</dt><dd>{fmtBothZones(tech.startedAt)}</dd>
          <dt>Duração</dt><dd>{tech.durationMs !== undefined ? fmtDuration(tech.durationMs) : tech.inProgress ? 'em curso' : 'não informado'}</dd>
          <dt>Tokens</dt><dd>{tech.tokens ? `${fmtInt(tech.tokens.input)} entrada · ${fmtInt(tech.tokens.output)} saída${tech.inProgress ? ' até agora' : ''}` : 'não informado'}</dd>
          <dt>Custo</dt>
          <dd>{tech.cost.known && tech.cost.usd !== undefined ? `≈ ${fmtUsd(tech.cost.usd)} (estimativa${tech.cost.partial ? ', parcial: faltam chamadas sem preço' : ''})` : tech.inProgress ? 'disponível ao concluir, se houver preço' : 'não informado'}</dd>
          <dt>Agentes</dt>
          <dd>{tech.agents.length ? `${tech.agents.map((a) => `${a.name}${a.version !== undefined ? ` v${a.version}` : ''}`).join(' → ')}${tech.graphVersion !== undefined ? ` · grafo v${tech.graphVersion}` : ''}` : 'não informado'}</dd>
          <dt>Ferramentas</dt>
          <dd>{tech.tools.length ? tech.tools.map((t, i) => <span key={i}>{i > 0 && ' · '}<span className="mono">{t.tool}</span>{t.durationMs !== undefined && Number.isFinite(t.durationMs) ? ` ${fmtDuration(t.durationMs)}` : ''}</span>) : 'nenhuma'}</dd>
          {tech.traceId && <><dt>Trace</dt><dd><a href={grafanaTraceUrl(tech.traceId)} target="_blank" rel="noreferrer">Abrir trace no Grafana<span className="sr-only"> (abre em nova aba)</span></a></dd></>}
          {tech.pricingVersion && <><dt>Preços</dt><dd className="mono">{tech.pricingVersion}</dd></>}
        </dl>
        <p className="small muted" style={{ margin: '12px 0 8px' }}>Dados de ferramentas mascarados pela política do backend. O raciocínio interno do modelo não é exibido.</p>
      </details>
    </>
  )
}

const KIND: Record<string, { label: string; icon: 'doc' | 'docCheck' | 'globe' } | undefined> = {
  curated: { label: 'Curada', icon: 'doc' }, official: { label: 'Fonte oficial', icon: 'docCheck' }, web: { label: 'Web', icon: 'globe' },
}

function Fontes({ run, evidence, highlight }: { run: RunStream; evidence: EvidenceState; highlight: string | null }) {
  const refs = useRef<Record<string, HTMLElement | null>>({})
  const done = Boolean(run.state.terminal || run.info?.status === 'completed')
  useEffect(() => {
    if (!highlight || !evidence.data) return
    const hit = evidence.data.cited.find((c) => matchesMarker(c, highlight))
    const el = hit && refs.current[hit.key]
    if (el) { el.scrollIntoView({ block: 'nearest' }); el.focus() }
  }, [highlight, evidence.data])

  if (!done) return <StatePanel kind="empty" title="As fontes aparecem quando a resposta termina" message="Enquanto a resposta é escrita, acompanhe os trechos encontrados na aba Andamento." />
  if (evidence.loading && !evidence.data) return <StatePanel kind="loading" what="as fontes" />
  if (evidence.problem) return <ProblemPanel problem={evidence.problem} what="as fontes" onRetry={evidence.reload} />
  const { cited = [], notUsed = [] } = evidence.data ?? {}
  if (cited.length === 0 && notUsed.length === 0) return <StatePanel kind="empty" title="Nenhuma fonte consultada" message="Esta resposta não usou a base de conhecimento nem a web." />
  const card = (it: EvidenceItem, used: boolean) => {
    const k = KIND[it.kind]
    return (
      <article key={it.key} ref={(el) => { refs.current[it.key] = el }} tabIndex={-1} className={`source${used ? '' : ' source--unused'}${highlight && matchesMarker(it, highlight) && used ? ' is-highlight' : ''}`}>
        <div className="source__head">
          {used && <span style={{ fontWeight: 600, color: 'var(--accent)' }}>[{it.marker}]<span className="sr-only"> citada</span></span>}
          <h3>{it.title}</h3>
          {k && <Chip icon={k.icon}>{k.label}</Chip>}
        </div>
        {it.text && <p className="source__text">“{it.text}”</p>}
        {it.note && <div className="note"><Icon name="info" size={14} /><span>{it.note}</span></div>}
        {it.limitations.map((l, i) => <div key={i} className="small tone-warning" style={{ display: 'flex', gap: 6 }}><Icon name="alert" size={14} /><span>{l}</span></div>)}
        <div className="small muted" style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: '4px 12px' }}>
          <span>{it.retrievedOn ? `Coletada em ${fmtDate(it.retrievedOn)}` : 'Data de coleta não informada'}</span>
          {it.url ? <ExternalLink href={it.url}>Abrir original</ExternalLink> : <span>Origem original indisponível</span>}
        </div>
        <details>
          <summary className="small muted" style={{ cursor: 'pointer' }}>Detalhes</summary>
          <dl className="kv" style={{ marginTop: 8, fontSize: 12, gridTemplateColumns: '72px minmax(0,1fr)' }}>
            {it.url && <><dt>Endereço</dt><dd style={{ wordBreak: 'break-all' }}>{it.url}</dd></>}
            <dt>Índice</dt><dd className="mono">{[it.indexVersion, it.chunkId ? `chunk ${it.chunkId}` : it.sourceId].filter(Boolean).join(' · ') || 'não informado'}</dd>
            <dt>Idioma</dt><dd>{[it.language, it.region].filter(Boolean).join(' · ') || 'não informado'}</dd>
          </dl>
        </details>
      </article>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <section aria-labelledby="titulo-usadas">
        <h2 id="titulo-usadas" className="sidepanel__h">Usadas na resposta ({cited.length})</h2>
        {cited.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhum trecho foi citado nesta resposta.</p> : <div className="sources">{cited.map((c) => card(c, true))}</div>}
      </section>
      <section aria-labelledby="titulo-nao-usadas">
        <h2 id="titulo-nao-usadas" className="sidepanel__h" style={{ color: 'var(--ink-2)' }}>Consultadas, mas não usadas ({notUsed.length})</h2>
        {notUsed.length === 0 ? <p className="muted" style={{ margin: 0 }}>Todos os trechos consultados foram usados.</p> : <div className="sources">{notUsed.map((c) => card(c, false))}</div>}
      </section>
    </div>
  )
}
