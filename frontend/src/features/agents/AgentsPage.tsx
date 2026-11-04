import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fieldErrors, toProblem, type ApiProblem } from '../../shared/api'
import { useCursorList, useDebounced, useDocumentTitle } from '../../shared/hooks'
import { Modal } from '../../shared/modal'
import { Button, Chip, Note, Pager, ProblemPanel, SearchBox, SelectField, StatePanel, TextAreaField, TextField } from '../../shared/ui'
import { fmtDateTime } from '../../shared/format'
import { MenuButton } from '../../app/Shell'
import { useAllAgents } from './hooks'
import { Tags } from './parts'
import { KIND_LABEL, type AgentFull, type AgentSummary } from './types'
import './agents.css'

function CreateDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const [f, setF] = useState({ name: '', description: '', kind: 'custom', instructions: '' })
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiProblem | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const instructionsRef = useRef<HTMLTextAreaElement>(null)

  const submit = async () => {
    const e: Record<string, string> = {}
    if (!f.name.trim()) e.name = 'Informe o nome do agente para criar o rascunho.'
    if (!f.instructions.trim()) e.instructions = 'Informe as instruções iniciais do agente.'
    setErrors(e); setFailure(null)
    if (e.name) return void nameRef.current?.focus()
    if (e.instructions) return void instructionsRef.current?.focus()
    setBusy(true)
    try {
      const a = await api<AgentFull>('/agents', { method: 'POST', body: { name: f.name.trim(), description: f.description.trim(), kind: f.kind, instructions: f.instructions } })
      navigate(`/agentes/${a.id}`)
    } catch (err) {
      const p = toProblem(err)
      const fe = fieldErrors(p)
      if (Object.keys(fe).length) setErrors(fe); else setFailure(p)
      setBusy(false)
    }
  }
  return (
    <Modal title="Criar agente" description="O identificador é gerado pelo servidor. Nada vale para execuções até você publicar." onClose={onClose}>
      <form style={{ display: 'flex', flexDirection: 'column', gap: 12 }} onSubmit={(e) => { e.preventDefault(); void submit() }} noValidate>
        <TextField ref={nameRef} label="Nome" value={f.name} error={errors.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <TextField label="Descrição" value={f.description} error={errors.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
        <SelectField label="Tipo de papel" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
          {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </SelectField>
        <TextAreaField ref={instructionsRef} label="Instruções de prompt" rows={5} value={f.instructions} error={errors.instructions} onChange={(e) => setF({ ...f, instructions: e.target.value })} />
        {failure && <Note tone="danger" icon="alert">{failure.message}{failure.requestId && <> request_id <code>{failure.requestId}</code></>}</Note>}
        <div className="dialog__actions">
          <Button onClick={onClose}>Cancelar</Button>
          <Button type="submit" variant="primary" busy={busy}>Criar rascunho</Button>
        </div>
      </form>
    </Modal>
  )
}

export default function AgentsPage() {
  useDocumentTitle('Agentes')
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  const [creating, setCreating] = useState(false)
  const q = useDebounced(search.trim(), 250)
  const list = useCursorList<AgentSummary>('/agents', { q, kind, status }, 'pages', 20)
  const { names } = useAllAgents()
  const filtered = Boolean(q || kind || status)
  const clear = () => { setSearch(''); setKind(''); setStatus('') }

  return (
    <main id="main" tabIndex={-1} className="page" aria-labelledby="agentes-titulo">
      <div className="page__head">
        <div className="head-title" style={{ alignItems: 'flex-start' }}>
          <MenuButton />
          <div>
            <h1 id="agentes-titulo">Agentes</h1>
            <p>Configure papéis, ferramentas e para quem cada agente pode delegar.</p>
          </div>
        </div>
        <div className="page__actions">
          <Chip icon="lock">Restrito a administradores</Chip>
          <Button variant="primary" icon="plus" onClick={() => setCreating(true)}>Criar agente</Button>
        </div>
      </div>

      <fieldset className="filters"><legend className="sr-only">Filtrar agentes</legend>
        <SearchBox value={search} onChange={setSearch} label="Buscar agentes" placeholder="Nome, descrição ou ID" />
        <SelectField className="field--select" label="Tipo" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">Todos</option>
          {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </SelectField>
        <SelectField className="field--select" label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Todos</option>
          <option value="draft">Rascunho</option>
          <option value="published">Publicado</option>
        </SelectField>
      </fieldset>

      {list.loading ? <section className="panel" aria-label="Lista de agentes"><div className="panel__body"><StatePanel kind="loading" what="agentes" /></div></section>
        : list.problem ? <section className="panel" aria-label="Lista de agentes"><div className="panel__body"><ProblemPanel problem={list.problem} what="os agentes" onRetry={list.reload} /></div></section>
        : list.items.length === 0 ? (
          <section className="panel" aria-label="Lista de agentes">
            <div className="panel__body">
              {filtered ? <StatePanel kind="noresult" query={q} onClear={clear} />
                : <StatePanel kind="empty" title="Nenhum agente ainda" message="Crie o primeiro agente para começar a montar o fluxo de atendimento." actions={<Button icon="plus" onClick={() => setCreating(true)}>Criar agente</Button>} />}
            </div>
          </section>
        ) : (
          <>
            <ul className="agent-cards" aria-label="Agentes do workspace">
              {list.items.map((a) => (
                <li key={a.id} className="agent-card">
                  <div className="agent-card__top">
                    <Chip>{KIND_LABEL[a.kind] ?? a.kind}</Chip>
                    <span className="small muted">{a.version ? `v${a.version}` : 'Sem versão publicada'}</span>
                  </div>
                  <h2 className="agent-card__name"><Link className="agent-card__link" to={`/agentes/${a.id}`}>{a.name}</Link></h2>
                  {a.description && <p className="agent-card__desc">{a.description}</p>}
                  <div className="agent-card__chips">
                    {a.status === 'published' ? <Chip tone="success" icon="check">Publicado</Chip> : <Chip tone="warning" icon="pencil">Rascunho · não publicado</Chip>}
                    {a.status === 'published' && a.has_unpublished_changes && <Chip tone="warning" icon="pencil">Rascunho rev. {a.revision} · não publicado</Chip>}
                    {a.enabled === false && <Chip icon="x">Inativo</Chip>}
                  </div>
                  <dl className="kv">
                    <dt>Ferramentas</dt><dd><Tags items={a.tools} /></dd>
                    <dt>Delega para</dt><dd>{a.delegates.length ? a.delegates.map((d) => names[d] ?? d).join(', ') : <span className="muted">Nenhum</span>}</dd>
                    <dt>Atualizado</dt><dd>{fmtDateTime(a.updated_at)}</dd>
                  </dl>
                  <div className="agent-card__foot">
                    <span className="id">{a.id}</span>
                    <span className="agent-card__cta" aria-hidden="true">Ver configuração</span>
                  </div>
                </li>
              ))}
            </ul>
            {(list.hasPrev || list.hasNext) && <Pager label="Paginação de agentes" hasPrev={list.hasPrev} hasNext={list.hasNext} onPrev={list.prev} onNext={list.next}
              summary={`Página ${list.page} · ${list.items.length} ${list.items.length === 1 ? 'agente' : 'agentes'} nesta página`} />}
          </>
        )}
      <p className="muted small" style={{ margin: 0 }}>IDs são gerados pelo servidor e únicos no workspace. Renomear não quebra vínculos.</p>
      {creating && <CreateDialog onClose={() => setCreating(false)} />}
    </main>
  )
}
