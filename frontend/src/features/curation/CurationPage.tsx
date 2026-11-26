import { useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../shared/api'
import { fmtDateTime, fmtInt } from '../../shared/format'
import { useDocumentTitle, useResource } from '../../shared/hooks'
import { Chip, ExternalLink, ProblemPanel, StatePanel } from '../../shared/ui'
import { Markdown } from '../../shared/markdown'
import { MenuButton } from '../../app/Shell'
import { useAllAgents } from '../agents/hooks'
import { outcome } from './outcome'
import './curation.css'

type Rate = number | null
export interface Curation {
  days: number
  runs: number
  judged: number
  revised: number
  skipped: number
  failed: number
  first_round_approval: Rate
  redone: number
  fixed: number
  mean_scores: Record<string, Rate>
  criteria: { criterion: string; label: string; below_max: number; rate: Rate }[]
  agents: { agent_id: string; judged: number; revised: number; rate: Rate }[]
  empty_searches: { query: string; count: number; last_at: string }[]
  examples: {
    run_id: string; conversation_id: string; question: string; created_at: string; revision?: string | null
    verdicts: string[]; issues: string[]; agents: string[]; failed: string[]; answers: string[]
    sources: { evidence_id: string; kind: string; source_id: string; title: string; url?: string | null }[]
  }[]
  suggestions: { kind: 'search' | 'prompt' | 'guardrail' | 'knowledge'; criterion: string | null; target: string; text: string }[]
}

const KIND_TAG: Record<string, string> = { search: 'Ajustar a busca', prompt: 'Ajustar as instruções', guardrail: 'Ajustar os guardrails', knowledge: 'Completar a base' }
const MAX_SOURCES = 3

const pct = (r: Rate) => (r === null ? '—' : `${Math.round(r * 100)}%`)
const PERIODS = [{ days: 1, label: 'Último dia' }, { days: 7, label: '7 dias' }, { days: 30, label: '30 dias' }]

export default function CurationPage() {
  useDocumentTitle('Curadoria')
  const [days, setDays] = useState(7)
  const res = useResource(`curation:${days}`, (signal) => api<Curation>(`/curation/reviews?days=${days}`, { signal }))
  const { names } = useAllAgents()

  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const i = PERIODS.findIndex((p) => p.days === days)
    const next = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? PERIODS.length - 1 : null
    if (next === null) return
    e.preventDefault()
    const d = PERIODS[(next + PERIODS.length) % PERIODS.length].days
    setDays(d)
    document.getElementById(`periodo-${d}`)?.focus()
  }

  return (
    <main id="main" tabIndex={-1} className="page" aria-labelledby="curadoria-titulo">
      <div className="page__head">
        <div className="head-title" style={{ alignItems: 'flex-start' }}>
          <MenuButton />
          <div>
            <h1 id="curadoria-titulo">Curadoria</h1>
            <p>Vereditos do Revisor, problemas recorrentes e sugestões de ajuste</p>
          </div>
        </div>
      </div>
      <div role="tablist" aria-label="Período" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {PERIODS.map((p) => (
          <button key={p.days} type="button" role="tab" id={`periodo-${p.days}`} className="tab" aria-selected={days === p.days} aria-controls="painel-curadoria"
            tabIndex={days === p.days ? 0 : -1} onKeyDown={onKey} onClick={() => setDays(p.days)}>{p.label}</button>
        ))}
      </div>
      <div role="tabpanel" id="painel-curadoria" aria-labelledby={`periodo-${days}`} className="cur">
        {res.problem && !res.data ? <ProblemPanel problem={res.problem} what="a curadoria" onRetry={res.reload} />
          : !res.data ? <StatePanel kind="loading" what="a curadoria" />
          : <CurationView data={res.data} names={names} />}
      </div>
    </main>
  )
}

export function CurationView({ data, names }: { data: Curation; names: Record<string, string> }) {
  const [only, setOnly] = useState<string | null>(null)
  if (data.judged === 0 && data.empty_searches.length === 0) {
    return <StatePanel kind="empty" title="Nenhum veredito no período"
      message="Os vereditos aparecem depois de conversas em que a resposta usou evidência (base de conhecimento, web ou dados do cliente). Converse com o assistente e volte aqui." />
  }
  const agentName = (id: string) => names[id] ?? id
  const stats: [string, string, string][] = [
    ['Respostas avaliadas', fmtInt(data.judged), 'Usaram a base, a web ou dados do cliente'],
    ['Aprovadas de primeira', pct(data.first_round_approval), 'Sem pedido de nova versão'],
    ['Pediram nova versão', fmtInt(data.revised), 'O Revisor apontou problema'],
    ['Revisões que resolveram', data.redone ? `${fmtInt(data.fixed)} de ${fmtInt(data.redone)}` : '—', 'Nova versão aprovada'],
    ['Sem avaliação', fmtInt(data.skipped), 'Sem evidência: saudação, recusa, encaminhamento'],
    ['Falhas do Revisor', fmtInt(data.failed), 'A resposta seguiu sem julgamento'],
  ]
  const label = (c: string) => data.criteria.find((x) => x.criterion === c)?.label ?? c
  const examples = only ? data.examples.filter((ex) => ex.failed?.includes(only)) : data.examples
  return (
    <>
      <dl className="cur__stats" aria-label="Resumo do período">
        {stats.map(([k, v, hint]) => <div key={k} className="cur__stat"><dt>{k}</dt><dd>{v}</dd><dd className="cur__hint">{hint}</dd></div>)}
      </dl>

      <section className="panel" aria-labelledby="sug-t">
        <div className="panel__head"><h2 id="sug-t">Sugestões de ajuste</h2></div>
        <div className="panel__body">
          {data.suggestions.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhum problema recorrente no período.</p> : (
            <ul className="cur__list">
              {data.suggestions.map((s, i) => (
                <li key={i}>
                  <strong>{KIND_TAG[s.kind] ?? s.kind}</strong>
                  {s.kind !== 'knowledge' && names[s.target] && <> de <Link to={`/agentes/${encodeURIComponent(s.target)}`}>{names[s.target]}</Link></>}{': '}
                  {s.text}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="panel" aria-labelledby="crit-t">
        <div className="panel__head"><h2 id="crit-t">Critérios que mais falham</h2></div>
        <div className="table-wrap">
          <table className="table">
            <caption className="sr-only">Critérios com nota abaixo do máximo na primeira rodada, do que mais falha para o que menos falha</caption>
            <thead><tr><th scope="col">Critério</th><th scope="col">Com problema</th><th scope="col">Taxa</th></tr></thead>
            <tbody>
              {data.criteria.map((c) => (
                <tr key={c.criterion}>
                  <td className="title">{c.below_max > 0
                    ? <button type="button" className="linklike" aria-pressed={only === c.criterion} onClick={() => setOnly(only === c.criterion ? null : c.criterion)}>
                        {c.label}<span className="sr-only">: ver as respostas com esse problema</span></button>
                    : c.label}</td>
                  <td>{fmtInt(c.below_max)}</td><td>{pct(c.rate)}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel" aria-labelledby="ag-t">
        <div className="panel__head"><h2 id="ag-t">Agentes nas respostas revisadas</h2></div>
        {data.agents.length === 0 ? <div className="panel__body"><p className="muted" style={{ margin: 0 }}>Sem agentes registrados no período.</p></div> : (
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Taxa de revisão por agente que participou das respostas, da maior para a menor</caption>
              <thead><tr><th scope="col">Agente</th><th scope="col">Avaliadas</th><th scope="col">Nova versão</th><th scope="col">Taxa</th></tr></thead>
              <tbody>
                {data.agents.map((a) => (
                  <tr key={a.agent_id}><td className="title">{agentName(a.agent_id)}</td><td>{fmtInt(a.judged)}</td><td>{fmtInt(a.revised)}</td><td>{pct(a.rate)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel" aria-labelledby="vazias-t">
        <div className="panel__head"><h2 id="vazias-t">Buscas sem evidência</h2><span className="small muted">Lacunas da base de conhecimento</span></div>
        {data.empty_searches.length === 0 ? <div className="panel__body"><p className="muted" style={{ margin: 0 }}>Todas as buscas na base trouxeram evidência.</p></div> : (
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Consultas à base que voltaram sem resultado, com quantidade e última ocorrência</caption>
              <thead><tr><th scope="col">Consulta</th><th scope="col">Vezes</th><th scope="col">Última vez</th></tr></thead>
              <tbody>
                {data.empty_searches.map((q) => <tr key={q.query}><td>{q.query}</td><td>{fmtInt(q.count)}</td><td>{fmtDateTime(q.last_at)}</td></tr>)}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel" aria-labelledby="ex-t">
        <div className="panel__head">
          <h2 id="ex-t">Respostas que pediram nova versão</h2>
          {only && <span className="small">Só com problema de {label(only)} · <button type="button" className="linklike" onClick={() => setOnly(null)}>ver todas</button></span>}
        </div>
        <div className="panel__body">
          {examples.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma resposta pediu nova versão no período.</p> : (
            <ul className="cur__list cur__examples">
              {examples.map((ex) => (
                <li key={ex.run_id}>
                  <div className="cur__ex-head">
                    <strong>{ex.question || 'Pergunta não registrada'}</strong>
                    <Link className="cur__open" to={`/conversas/${encodeURIComponent(ex.conversation_id)}`}
                      aria-label={`Abrir conversa: ${ex.question || 'pergunta não registrada'}`}>Abrir conversa</Link>
                  </div>
                  <div className="small muted">
                    {fmtDateTime(ex.created_at)} · {outcome(ex.verdicts, ex.revision)}
                    {ex.agents.length > 0 && <> · {ex.agents.map(agentName).join(', ')}</>}
                  </div>
                  {(ex.failed ?? []).length > 0 && <div className="cur__chips">{ex.failed.map((c) => <Chip key={c}>{label(c)}</Chip>)}</div>}
                  {ex.issues.length > 0 && <ul className="small">{ex.issues.map((x, i) => <li key={i}>{x}</li>)}</ul>}
                  {(ex.answers ?? []).length > 0 && (
                    <details className="cur__answers small">
                      <summary>Ver a resposta antes e depois</summary>
                      <div className="cur__versions">
                        {ex.answers.map((a, i) => (
                          <div key={i}><div className="muted">{i === 0 ? 'Primeira versão (o cliente não viu)' : 'Versão enviada'}</div><Markdown text={a} /></div>
                        ))}
                      </div>
                    </details>
                  )}
                  {ex.sources.length > 0 && (
                    <div className="small">Fontes:{' '}
                      {ex.sources.slice(0, MAX_SOURCES).map((s, i) => (
                        <span key={s.evidence_id}>{i > 0 && ', '}{s.url ? <ExternalLink href={s.url}>{s.title || s.url}</ExternalLink> : s.title || s.source_id}</span>
                      ))}
                      {ex.sources.length > MAX_SOURCES && <span className="muted"> e mais {ex.sources.length - MAX_SOURCES}</span>}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </>
  )
}
