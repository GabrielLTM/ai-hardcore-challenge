import { useState, type KeyboardEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, readPage, toProblem, type ApiProblem } from '../../shared/api'
import { fmtDateTimeZone } from '../../shared/format'
import { useDocumentTitle, useResource } from '../../shared/hooks'
import { Modal } from '../../shared/modal'
import { useToast } from '../../shared/toast-context'
import { Button, Chip, Note, ProblemPanel, SelectField, StatePanel } from '../../shared/ui'
import { MenuButton } from '../../app/Shell'
import { diffContent, lineDiff, type Change } from './diff'
import { useAllAgents } from './hooks'
import { DiffLines, DiffView } from './parts'
import { toForm, type AgentContent, type AgentFull, type VersionRow } from './types'
import './agents.css'

type Tab = 'agente' | 'prompt' | 'snapshots'
const TABS: { id: Tab; label: string }[] = [{ id: 'agente', label: 'Agente' }, { id: 'prompt', label: 'Prompt' }, { id: 'snapshots', label: 'Snapshots do grafo' }]
interface PromptRow { version: number; instructions: string; created_by?: string; created_at?: string }
interface GraphRow { id: string; version: number; summary?: string; created_by?: string; created_at?: string; agent_ids?: string[]; nodes?: Record<string, number> }

const DRAFT = 'draft'

export default function VersionsPage() {
  const { agentId = '' } = useParams()
  const [tab, setTab] = useState<Tab>('agente')
  const agent = useResource(`agent:${agentId}`, (signal) => api<AgentFull>(`/agents/${agentId}`, { signal }))
  useDocumentTitle(`Versões${agent.data ? ` · ${agent.data.name}` : ''}`)

  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const i = TABS.findIndex((t) => t.id === tab)
    const next = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : null
    if (next === null) return
    e.preventDefault()
    const t = TABS[(next + TABS.length) % TABS.length].id
    setTab(t)
    document.getElementById(`tab-${t}`)?.focus()
  }

  return (
    <main id="main" tabIndex={-1} className="page" aria-labelledby="versoes-titulo">
      <nav aria-label="Trilha de navegação" className="crumbs">
        <ol>
          <li><Link to="/agentes">Agentes</Link></li><li aria-hidden="true">/</li>
          <li><Link to={`/agentes/${agentId}`}>{agent.data?.name ?? agentId}</Link></li><li aria-hidden="true">/</li>
          <li aria-current="page">Versões</li>
        </ol>
      </nav>
      <div className="head-title" style={{ flexWrap: 'wrap' }}>
        <MenuButton />
        <h1 id="versoes-titulo" style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>Histórico de versões</h1>
        <code>{agentId}</code>
      </div>
      <div role="tablist" aria-label="Tipo de versão" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" id={`tab-${t.id}`} className="tab" aria-selected={tab === t.id} aria-controls="painel-versoes" tabIndex={tab === t.id ? 0 : -1} onKeyDown={onKey} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </div>
      <Note>Versões publicadas são imutáveis: não podem ser editadas nem excluídas. Restaurar cria uma nova versão.</Note>
      <div role="tabpanel" id="painel-versoes" aria-labelledby={`tab-${tab}`} tabIndex={0}>
        {agent.problem && !agent.data ? <ProblemPanel problem={agent.problem} what="o agente" onRetry={agent.reload} />
          : !agent.data ? <StatePanel kind="loading" what="o agente" />
          : tab === 'agente' ? <AgentVersions agent={agent.data} reloadAgent={agent.reload} />
          : tab === 'prompt' ? <PromptVersions promptId={agent.data.prompt_id} />
          : <GraphSnapshots />}
      </div>
    </main>
  )
}

function AgentVersions({ agent, reloadAgent }: { agent: AgentFull; reloadAgent: () => void }) {
  const toast = useToast()
  const { names } = useAllAgents()
  const list = useResource(`versions:${agent.id}`, async (signal) => readPage<VersionRow>(await api(`/agents/${agent.id}/versions?limit=100`, { signal })).items)
  const rows = list.data ?? []
  const [pick, setPick] = useState<{ from?: string; to?: string; selected?: string } | null>(null)
  const [restoring, setRestoring] = useState<VersionRow | null>(null)

  const current = rows.find((r) => r.is_current) ?? rows[0]
  const options = [{ id: DRAFT, label: `Rascunho rev. ${agent.revision}` }, ...rows.map((r) => ({ id: String(r.version), label: `v${r.version}${r.is_current ? ' (atual)' : ''}` }))]
  const selected = pick?.selected ?? (current ? String(current.version) : DRAFT)
  const to = pick?.to ?? selected
  const prev = rows.find((r) => String(r.version) === to) ? rows.find((r) => r.version === Number(to) - 1) : undefined
  const from = pick?.from ?? (to === DRAFT ? (current ? String(current.version) : undefined) : prev ? String(prev.version) : undefined)

  const diff = useResource(from && to ? `diff:${agent.id}:${from}:${to}:${agent.revision}` : null, async (signal): Promise<{ changes: Change[]; identical: boolean }> => {
    const content = async (v: string): Promise<AgentContent> => (v === DRAFT ? toForm(agent) : api<AgentContent>(`/agents/${agent.id}/versions/${v}`, { signal }))
    if (from === DRAFT || to === DRAFT) {
      const [a, b] = await Promise.all([content(from!), content(to)])
      const changes = diffContent(a, b)
      return { changes, identical: changes.length === 0 }
    }
    const r = await api<{ changes: Change[]; identical?: boolean }>(`/agents/${agent.id}/versions/${from}/diff/${to}`, { signal })
    return { changes: r.changes ?? [], identical: Boolean(r.identical) }
  })
  const meta = rows.find((r) => String(r.version) === to)
  const nextVersion = (rows[0]?.version ?? 0) + 1

  if (list.problem && !list.data) return <ProblemPanel problem={list.problem} what="as versões" onRetry={list.reload} />
  if (!list.data) return <StatePanel kind="loading" what="as versões" />
  return (
    <div className="versions">
      <section aria-labelledby="h-lista">
        <h2 id="h-lista" style={{ fontSize: 16, margin: '0 0 8px' }}>Versões do agente</h2>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <li>
            <button type="button" className="version-item" aria-pressed={selected === DRAFT} onClick={() => setPick({ selected: DRAFT })}>
              <span style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}><strong>Rascunho rev. {agent.revision}</strong>{agent.has_unpublished_changes === false ? <Chip icon="check">Igual ao publicado</Chip> : <Chip tone="warning" icon="pencil">Rascunho · não publicado</Chip>}</span>
              <span className="small muted">{agent.updated_by ?? 'autor não informado'} · {fmtDateTimeZone(agent.updated_at)}</span>
            </button>
          </li>
          {rows.length === 0 && <li className="muted">Nenhuma versão publicada ainda.</li>}
          {rows.map((r) => (
            <li key={r.version}>
              <button type="button" className="version-item" aria-pressed={selected === String(r.version)} onClick={() => setPick({ selected: String(r.version) })}>
                <span style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  <strong>v{r.version}</strong>
                  <Chip tone="success" icon="check">{r.is_current ? 'Publicada · Atual' : 'Publicada'}</Chip>
                  {selected === String(r.version) && <Chip tone="progress" icon="check">Selecionada</Chip>}
                </span>
                <span className="small muted">{r.created_by ?? 'autor não informado'} · {fmtDateTimeZone(r.created_at)}</span>
                {r.summary && <span className="small">{r.summary}</span>}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="h-diff" style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
        <div className="panel">
          <div className="panel__head">
            <h2 id="h-diff">{from && to ? `Comparando ${label(from)} → ${label(to)}` : `Detalhe de ${label(to)}`}</h2>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <SelectField label="De" value={from ?? ''} onChange={(e) => setPick({ ...pick, from: e.target.value })}>
                {!from && <option value="">Selecione</option>}
                {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </SelectField>
              <SelectField label="Para" value={to} onChange={(e) => setPick({ ...pick, to: e.target.value })}>
                {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </SelectField>
            </div>
          </div>
          <div className="panel__body">
            {meta && <p className="muted" style={{ margin: 0 }}>Publicado por {meta.created_by ?? 'autor não informado'} em {fmtDateTimeZone(meta.created_at)}{meta.restored_from ? ` · restaurada da v${meta.restored_from}` : ''}</p>}
            {!from ? <p className="muted" style={{ margin: 0 }}>Esta é a primeira versão: não há versão anterior para comparar. Escolha outra em “De”.</p>
              : diff.problem ? <ProblemPanel problem={diff.problem} what="a comparação" onRetry={diff.reload} />
              : !diff.data ? <StatePanel kind="loading" what="a comparação" />
              : <DiffView changes={diff.data.changes} names={names} />}
            {diff.data && diff.data.changes.length > 0 && <p className="small muted" style={{ margin: 0 }}>Legenda: + adicionado · − removido · linhas sem marca não mudaram.</p>}
            {meta && !meta.is_current && (
              <div><Button icon="refresh" onClick={() => setRestoring(meta)}>Restaurar v{meta.version}</Button></div>
            )}
          </div>
        </div>
      </section>

      {restoring && (
        <RestoreDialog agent={agent} version={restoring} next={nextVersion} current={current?.version}
          onClose={() => setRestoring(null)}
          onDone={(v) => { setRestoring(null); setPick(null); list.reload(); reloadAgent(); toast(`Criada a v${v} a partir da v${restoring.version}`) }} />
      )}
    </div>
  )
}

const label = (id: string) => (id === DRAFT ? 'Rascunho' : `v${id}`)

function RestoreDialog({ agent, version, next, current, onClose, onDone }: { agent: AgentFull; version: VersionRow; next: number; current?: number; onClose: () => void; onDone: (v: number) => void }) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<ApiProblem | null>(null)
  const go = async () => {
    setBusy(true); setProblem(null)
    try {
      const out = await api<{ version: number }>(`/agents/${agent.id}/versions/${version.version}/restore`, { method: 'POST', headers: { 'If-Match': `"${agent.revision}"` }, body: {} })
      onDone(out.version)
    } catch (e) { setProblem(toProblem(e)); setBusy(false) }
  }
  return (
    <Modal title="Confirmar restauração" onClose={onClose}
      description={`Restaurar cria a v${next} com o conteúdo da v${version.version}.${current ? ` A v${current} permanece no histórico.` : ''} O rascunho atual será substituído por esse conteúdo.`}>
      {problem && <Note tone="danger" icon="alert">{problem.status === 412 ? 'O rascunho mudou desde que esta página abriu. Recarregue a página e tente de novo.' : problem.message}{problem.requestId && <> request_id <code>{problem.requestId}</code></>}</Note>}
      <div className="dialog__actions">
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="primary" busy={busy} onClick={() => void go()}>Criar v{next} a partir da v{version.version}</Button>
      </div>
    </Modal>
  )
}

function PromptVersions({ promptId }: { promptId?: string }) {
  const list = useResource(promptId ? `prompts:${promptId}` : null, async (signal) => readPage<PromptRow>(await api(`/prompts/${promptId}/versions?limit=100`, { signal })).items)
  const [sel, setSel] = useState<number | null>(null)
  if (!promptId) return <StatePanel kind="empty" title="Prompt sem identificador" message="Este agente não informou o prompt versionado." />
  if (list.problem) return <ProblemPanel problem={list.problem} what="as versões do prompt" onRetry={list.reload} />
  if (!list.data) return <StatePanel kind="loading" what="as versões do prompt" />
  const rows = list.data
  if (rows.length === 0) return <StatePanel kind="empty" title="Nenhuma versão de prompt" message="O prompt é versionado quando o agente é publicado." />
  const cur = rows.find((r) => r.version === sel) ?? rows[0]
  const before = rows.find((r) => r.version === cur.version - 1)
  return (
    <div className="versions">
      <section aria-labelledby="h-prompts">
        <h2 id="h-prompts" style={{ fontSize: 16, margin: '0 0 8px' }}>Versões do prompt</h2>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {rows.map((r) => (
            <li key={r.version}>
              <button type="button" className="version-item" aria-pressed={cur.version === r.version} onClick={() => setSel(r.version)}>
                <strong>Prompt v{r.version}</strong>
                <span className="small muted">{r.created_by ?? 'autor não informado'} · {fmtDateTimeZone(r.created_at)}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section className="panel" aria-labelledby="h-prompt-diff">
        <div className="panel__head"><h2 id="h-prompt-diff">{before ? `Prompt v${before.version} → v${cur.version}` : `Prompt v${cur.version}`}</h2></div>
        <div className="panel__body">
          {before ? <DiffLines lines={lineDiff(before.instructions, cur.instructions)} /> : <pre className="mono" style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{cur.instructions}</pre>}
        </div>
      </section>
    </div>
  )
}

function GraphSnapshots() {
  const { names } = useAllAgents()
  const list = useResource('graph-versions', async (signal) => readPage<GraphRow>(await api('/graph/versions?limit=100', { signal })).items)
  const [sel, setSel] = useState<number | null>(null)
  if (list.problem) return <ProblemPanel problem={list.problem} what="os snapshots" onRetry={list.reload} />
  if (!list.data) return <StatePanel kind="loading" what="os snapshots" />
  if (list.data.length === 0) return <StatePanel kind="empty" title="Nenhum snapshot ainda" message="Cada publicação cria um snapshot imutável do grafo de agentes." />
  const cur = list.data.find((r) => r.version === sel) ?? list.data[0]
  return (
    <div className="versions">
      <section aria-labelledby="h-snaps">
        <h2 id="h-snaps" style={{ fontSize: 16, margin: '0 0 8px' }}>Snapshots do grafo</h2>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {list.data.map((r) => (
            <li key={r.version}>
              <button type="button" className="version-item" aria-pressed={cur.version === r.version} onClick={() => setSel(r.version)}>
                <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}><strong>Grafo v{r.version}</strong>{r.version === list.data?.[0].version && <Chip tone="success" icon="check">Atual</Chip>}</span>
                <span className="small muted">{r.created_by ?? 'autor não informado'} · {fmtDateTimeZone(r.created_at)}</span>
                {r.summary && <span className="small">{r.summary}</span>}
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section className="panel" aria-labelledby="h-snap">
        <div className="panel__head"><h2 id="h-snap">Grafo v{cur.version}</h2></div>
        <div className="table-wrap">
          <table className="table">
            <caption className="sr-only">Versão publicada de cada agente no grafo v{cur.version}</caption>
            <thead><tr><th scope="col">Agente</th><th scope="col">Versão publicada</th></tr></thead>
            <tbody>
              {Object.entries(cur.nodes ?? {}).map(([id, version]) => (
                <tr key={id}>
                  <td><div className="title">{names[id] ?? id}</div><div className="id">{id}</div></td>
                  <td>v{version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
