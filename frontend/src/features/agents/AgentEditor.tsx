import { useEffect, useRef, useState, type RefObject } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, apiFull, fieldErrors, toProblem, type ApiProblem } from '../../shared/api'
import { fmtDateTimeZone, fmtTime } from '../../shared/format'
import { useDocumentTitle, useResource } from '../../shared/hooks'
import { Icon } from '../../shared/icons'
import { Modal } from '../../shared/modal'
import { useToast } from '../../shared/toast-context'
import { Button, ButtonLink, Chip, Note, ProblemPanel, SelectField, StatePanel, TextAreaField, TextField } from '../../shared/ui'
import { MenuButton } from '../../app/Shell'
import { diffContent, type Change } from './diff'
import { useAllAgents } from './hooks'
import { DiffView, Tags } from './parts'
import {
  fmtLimits, KIND_LABEL, sameContent, TOOL_CATALOG, toForm, toPayload,
  type AgentContent, type AgentFull, type Relationships, type Validation,
} from './types'
import './agents.css'

const MAX_TOOL_CALLS = 50
const MAX_OUTPUT_TOKENS = 64000

const readValidation = (raw: unknown): Validation => {
  const r = (raw ?? {}) as Partial<Validation>
  return { valid: r.valid !== false && !(r.errors?.length), graph_revision: r.graph_revision, stale: r.stale, errors: r.errors ?? [], cycle: r.cycle }
}

function validateLocal(f: AgentContent): Record<string, string> {
  const e: Record<string, string> = {}
  if (!f.name.trim()) e.name = 'Informe o nome do agente para salvar o rascunho.'
  if (!f.instructions.trim()) e.instructions = 'Informe as instruções de prompt do agente.'
  if (!f.model.trim()) e.model = 'Informe o modelo.'
  const t = f.limits.max_tool_calls, o = f.limits.max_output_tokens
  if (t !== undefined && (!Number.isInteger(t) || t < 1 || t > MAX_TOOL_CALLS)) e.max_tool_calls = `Use um inteiro de 1 a ${MAX_TOOL_CALLS}.`
  if (o !== undefined && (!Number.isInteger(o) || o < 1 || o > MAX_OUTPUT_TOKENS)) e.max_output_tokens = `Use um inteiro de 1 a ${MAX_OUTPUT_TOKENS}.`
  return e
}
const FIELD_ORDER = ['name', 'model', 'instructions', 'max_tool_calls', 'max_output_tokens']

export default function AgentEditor() {
  const { agentId } = useParams()
  const res = useResource(agentId ? `agent:${agentId}` : null, (signal) => api<AgentFull>(`/agents/${agentId}`, { signal }))
  useDocumentTitle(res.data?.name ?? 'Agente')
  return (
    <main id="main" tabIndex={-1} className="page" aria-labelledby="agente-titulo">
      {res.data ? <EditorForm key={res.data.id} initial={res.data} />
        : (
          <>
            <div className="head-title"><MenuButton /><h1 id="agente-titulo" style={{ margin: 0, fontSize: 22 }}>Agente</h1></div>
            {res.problem
              ? res.problem.status === 404
                ? <StatePanel kind="empty" title="Agente não encontrado" message="O agente pode ter sido removido ou o endereço está incorreto." actions={<Link className="btn" to="/agentes">Voltar para Agentes</Link>} />
                : <ProblemPanel problem={res.problem} what="o agente" onRetry={res.reload} />
              : <StatePanel kind="loading" what="o agente" />}
          </>
        )}
    </main>
  )
}

function EditorForm({ initial }: { initial: AgentFull }) {
  const toast = useToast()
  const { agents, names } = useAllAgents()
  const [baseline, setBaseline] = useState(initial)
  const [form, setForm] = useState<AgentContent>(() => toForm(initial))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<ApiProblem | null>(null)
  const [validation, setValidation] = useState<Validation | null>(null)
  const [busy, setBusy] = useState<'save' | 'validate' | 'publish' | null>(null)
  const [publishing, setPublishing] = useState<{ changes: Change[]; error?: string } | null>(null)
  const [conflict, setConflict] = useState<AgentFull | null>(null)
  const fields = useRef<Record<string, HTMLElement | null>>({})
  const publishBtn = useRef<HTMLButtonElement>(null)

  const saved = toForm(baseline)
  const dirty = !sameContent(form, saved)
  const published = baseline.published
  const rel = useResource(`rel:${baseline.id}:${baseline.revision}`, (signal) => api<Relationships>(`/agents/${baseline.id}/relationships?state=draft`, { signal }))
  const changed = (k: keyof AgentContent) => JSON.stringify(form[k]) !== JSON.stringify(saved[k])

  useEffect(() => {
    if (!dirty) return
    const guard = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])

  const set = <K extends keyof AgentContent>(k: K, v: AgentContent[K]) => setForm((f) => ({ ...f, [k]: v }))
  const toggle = (k: 'tools' | 'delegates', id: string, on: boolean) => set(k, on ? [...form[k], id] : form[k].filter((x) => x !== id))
  const setLimit = (k: keyof AgentContent['limits'], raw: string) => {
    const limits = { ...form.limits }
    if (raw.trim() === '') delete limits[k]; else limits[k] = Number(raw)
    set('limits', limits)
  }
  const focusFirst = (e: Record<string, string>) => {
    const key = FIELD_ORDER.find((k) => e[k])
    if (key) fields.current[key]?.focus()
  }

  // delegáveis que fecham ciclo (ou apontam para destino inválido) na última validação
  const bad = new Map<string, string>()
  if (validation && !validation.valid) {
    for (const issue of validation.errors) {
      const path = issue.path ?? validation.cycle ?? []
      const label = (id: string) => names[id] ?? id
      for (const d of form.delegates) {
        if (path.includes(d) && d !== baseline.id) bad.set(d, issue.code === 'cycle' || path.length > 2 ? `Cria o ciclo ${path.map(label).join(' → ')}.` : issue.message ?? 'Vínculo inválido.')
        else if (issue.agent_id === d) bad.set(d, issue.message ?? 'Vínculo inválido.')
      }
    }
  }
  const cyclePath = validation && !validation.valid ? validation.cycle ?? validation.errors.find((i) => i.path?.length)?.path ?? null : null

  const reveal = () => requestAnimationFrame(() => {
    const el = document.querySelector<HTMLElement>('[data-delegate][aria-invalid="true"]') ?? document.getElementById('erro-ciclo')
    el?.focus()
  })

  const onWriteError = async (err: unknown) => {
    const p = toProblem(err)
    if (p.status === 412) {
      try { setConflict(await api<AgentFull>(`/agents/${baseline.id}`)); return } catch (e) { setFormError(toProblem(e)); return }
    }
    const fe = fieldErrors(p)
    if (p.status === 422 && Object.keys(fe).length) { setErrors(fe); focusFirst(fe); return }
    setFormError(p)
  }

  const save = async (): Promise<boolean> => {
    setFormError(null)
    const e = validateLocal(form)
    setErrors(e)
    if (Object.keys(e).length) { focusFirst(e); return false }
    setBusy('save')
    try {
      const { data } = await apiFull<AgentFull>(`/agents/${baseline.id}`, { method: 'PATCH', headers: { 'If-Match': `"${baseline.revision}"` }, body: toPayload(form) })
      setBaseline(data)
      toast(`Rascunho salvo às ${fmtTime(new Date().toISOString())}`)
      return true
    } catch (err) { await onWriteError(err); return false } finally { setBusy(null) }
  }

  const validate = async (): Promise<Validation | null> => {
    setFormError(null)
    setBusy('validate')
    try {
      const v = readValidation(await api<Validation>(`/agents/${baseline.id}/relationships/validate`, {
        method: 'POST', body: { delegates: form.delegates, tools: toPayload(form).tools, enabled: form.enabled },
      }))
      setValidation(v)
      if (v.valid) toast('Relações válidas: nenhum ciclo ou destino ausente.')
      else reveal()
      return v
    } catch (err) { await onWriteError(err); return null } finally { setBusy(null) }
  }

  const doPublish = async (summary: string) => {
    setBusy('publish')
    try {
      const out = await api<{ agent: AgentFull; version: number }>(`/agents/${baseline.id}/publish`, {
        method: 'POST', headers: { 'If-Match': `"${baseline.revision}"` }, body: summary.trim() ? { summary: summary.trim() } : {},
      })
      setBaseline(out.agent)
      setForm(toForm(out.agent))
      setValidation(null); setPublishing(null)
      toast(`Publicado v${out.version}`)
    } catch (err) {
      const p = toProblem(err)
      const d = (p.details ?? {}) as { errors?: Validation['errors']; cycle?: string[] | null }
      if (p.code === 'graph_invalid') { setValidation({ valid: false, errors: d.errors ?? [], cycle: d.cycle }); setPublishing(null); setFormError(p); reveal() }
      else if (p.status === 412) { setPublishing(null); await onWriteError(err) }
      else setPublishing((s) => (s ? { ...s, error: `${p.message}${p.requestId ? ` (request_id ${p.requestId})` : ''}` } : s))
    } finally { setBusy(null) }
  }

  const requestPublish = async () => {
    const v = await validate()
    if (!v?.valid) return
    const changes = diffContent(published ?? {}, toPayload(form))
    const touchesAccess = changes.some((c) => c.field === 'tools' || c.field === 'delegates')
    if (touchesAccess || !published) setPublishing({ changes })
    else await doPublish('')
  }

  const nextVersion = (published?.version ?? 0) + 1
  const inbound = rel.data?.inbound ?? []
  const options = agents.filter((a) => a.id !== baseline.id)
  const unknown = form.delegates.filter((d) => !options.some((o) => o.id === d))
  const publishHint = dirty ? 'Salve o rascunho antes de publicar.' : validation && !validation.valid ? 'Corrija os vínculos inválidos para publicar.' : published && baseline.has_unpublished_changes === false ? 'Não há alterações para publicar.' : ''
  const publishDisabled = dirty || Boolean(validation && !validation.valid) || Boolean(published && baseline.has_unpublished_changes === false)

  return (
    <>
      <nav aria-label="Trilha de navegação" className="crumbs">
        <ol><li><Link to="/agentes">Agentes</Link></li><li aria-hidden="true">/</li><li aria-current="page">{baseline.name}</li></ol>
      </nav>
      <header className="page__head">
        <div className="head-title" style={{ alignItems: 'flex-start' }}>
          <MenuButton />
          <div>
            <h1 id="agente-titulo" style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>{baseline.name}</h1>
            <div className="small muted"><span className="mono">{baseline.id}</span> · gerado pelo servidor</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
              <Chip tone="warning" icon="pencil">Rascunho rev. {baseline.revision}{dirty ? ' · alterações não salvas' : ''}</Chip>
              {published ? <Chip tone="success" icon="check">Publicado v{published.version}</Chip> : <Chip icon="circle">Ainda não publicado</Chip>}
              {!form.enabled && <Chip icon="x">Inativo</Chip>}
            </div>
          </div>
        </div>
        <div className="page__actions">
          <ButtonLink to={`/agentes/${baseline.id}/versoes`} icon="clock">Histórico de versões</ButtonLink>
          <Button busy={busy === 'save'} disabled={busy !== null || !dirty} onClick={() => void save()}>Salvar rascunho</Button>
          <Button icon="check" busy={busy === 'validate'} disabled={busy !== null} onClick={() => void validate()}>Validar relações</Button>
          <Button ref={publishBtn} variant="primary" busy={busy === 'publish'} disabled={busy !== null || publishDisabled} aria-describedby={publishHint ? 'publicar-dica' : undefined} onClick={() => void requestPublish()}>Publicar</Button>
        </div>
      </header>
      {publishHint && <p id="publicar-dica" className="muted small" style={{ margin: 0, textAlign: 'right' }}>{publishHint}</p>}

      {formError && (
        <div role="alert" className="alert-box" style={{ alignItems: 'center' }}>
          <Icon name="alert" />
          <span>{formError.message}{formError.requestId && <> request_id <code>{formError.requestId}</code></>}</span>
        </div>
      )}
      {cyclePath && (
        <div id="erro-ciclo" role="alert" tabIndex={-1} className="alert-box">
          <Icon name="alert" />
          <div>
            <strong>A publicação criaria um ciclo</strong>
            <ol className="cycle-path" aria-label="Caminho do ciclo">
              {cyclePath.map((id, i) => (
                <li key={i}><span className="node"><Icon name="agents" size={14} />{names[id] ?? id}</span>{i < cyclePath.length - 1 && <span aria-hidden="true">→</span>}</li>
              ))}
            </ol>
            <p style={{ margin: 0 }}>Remova um destes agentes dos agentes delegáveis ou altere o vínculo de origem.{published ? ` A versão publicada v${published.version} continua ativa.` : ''}</p>
            <p style={{ margin: '8px 0 0' }}><a href="#delegaveis" onClick={(e) => { e.preventDefault(); document.getElementById('delegaveis')?.scrollIntoView({ block: 'center' }); reveal() }}>Ir para Agentes delegáveis</a></p>
          </div>
        </div>
      )}
      {validation && !validation.valid && !cyclePath && (
        <div id="erro-ciclo" role="alert" tabIndex={-1} className="alert-box">
          <Icon name="alert" />
          <div><strong>Há vínculos inválidos</strong><ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{validation.errors.map((e, i) => <li key={i}>{e.message}</li>)}</ul></div>
        </div>
      )}
      {validation?.stale && <Note tone="warning">O grafo publicado mudou desde a última validação. Valide de novo antes de publicar.</Note>}

      <div className="editor">
        <form className="panel" aria-labelledby="h-rascunho" noValidate onSubmit={(e) => { e.preventDefault(); void save() }}>
          <div className="panel__head"><h2 id="h-rascunho">Rascunho rev. {baseline.revision}</h2></div>
          <div className="panel__body">
            <div className="form-grid">
              <TextField ref={(el) => { fields.current.name = el }} label="Nome" value={form.name} error={errors.name} onChange={(e) => set('name', e.target.value)} maxLength={120} />
              <SelectField label="Tipo de papel" value={form.kind} onChange={(e) => set('kind', e.target.value)}>
                {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                {!(form.kind in KIND_LABEL) && <option value={form.kind}>{form.kind}</option>}
              </SelectField>
              <TextField ref={(el) => { fields.current.model = el }} label="Modelo" className="mono" list="modelos" value={form.model} error={errors.model} onChange={(e) => set('model', e.target.value)} />
              <datalist id="modelos"><option value="gpt-6-luna">gpt-6-luna</option>{baseline.model && baseline.model !== 'gpt-6-luna' && <option value={baseline.model}>{baseline.model}</option>}</datalist>
            </div>
            <TextField label="Descrição" value={form.description} error={errors.description} onChange={(e) => set('description', e.target.value)} maxLength={500} />
            <div className="checkbox-row" style={{ padding: 0 }}>
              <input id="f-ativo" type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} />
              <label htmlFor="f-ativo"><span>Agente ativo</span><span className="desc">Agentes inativos não recebem delegações.</span></label>
            </div>
            <div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                <span className="field__label" id="lbl-prompt">Instruções de prompt</span>
                {changed('instructions') && <Chip tone="warning" icon="pencil">Alterado</Chip>}
              </div>
              <TextAreaField ref={(el) => { fields.current.instructions = el }} label="Instruções de prompt" labelHidden rows={9} value={form.instructions} error={errors.instructions}
                hint={`${form.instructions.length.toLocaleString('pt-BR')} caracteres. Não inclua senhas ou chaves.`} onChange={(e) => set('instructions', e.target.value)} className="mono" />
            </div>
            <fieldset className="group">
              <legend>Limites</legend>
              <div className="form-grid">
                <TextField ref={(el) => { fields.current.max_tool_calls = el }} label="Chamadas de tool" type="number" min={1} max={MAX_TOOL_CALLS} inputMode="numeric"
                  value={form.limits.max_tool_calls ?? ''} error={errors.max_tool_calls} hint="Vazio usa o padrão do servidor." onChange={(e) => setLimit('max_tool_calls', e.target.value)} />
                <TextField ref={(el) => { fields.current.max_output_tokens = el }} label="Tokens de saída" type="number" min={1} max={MAX_OUTPUT_TOKENS} inputMode="numeric"
                  value={form.limits.max_output_tokens ?? ''} error={errors.max_output_tokens} hint="Vazio usa o padrão do servidor." onChange={(e) => setLimit('max_output_tokens', e.target.value)} />
              </div>
              <p className="muted small" style={{ margin: '8px 0 0' }}>Profundidade de delegação, número de delegações e tempo limite seguem os padrões da execução.</p>
            </fieldset>
            <fieldset className="group">
              <legend>Ferramentas {changed('tools') && <Chip tone="warning" icon="pencil">Alterado</Chip>}</legend>
              {TOOL_CATALOG.map((t) => (
                <div key={t.name} className="checkbox-row">
                  <input id={`t-${t.name}`} type="checkbox" checked={form.tools.includes(t.name)} onChange={(e) => toggle('tools', t.name, e.target.checked)} />
                  <label htmlFor={`t-${t.name}`}><span className="mono">{t.name}</span><span className="desc">{t.description}</span></label>
                </div>
              ))}
              {form.tools.filter((t) => !TOOL_CATALOG.some((c) => c.name === t)).map((t) => (
                <div key={t} className="checkbox-row">
                  <input id={`t-${t}`} type="checkbox" checked onChange={(e) => toggle('tools', t, e.target.checked)} />
                  <label htmlFor={`t-${t}`}><span className="mono">{t}</span><span className="desc">Ferramenta não listada no catálogo.</span></label>
                </div>
              ))}
            </fieldset>
            <fieldset className="group" id="delegaveis" tabIndex={-1}>
              <legend>Agentes delegáveis {changed('delegates') && <Chip tone="warning" icon="pencil">Alterado</Chip>}</legend>
              <p className="muted" style={{ margin: '0 0 4px' }}>Escolha para quais agentes {baseline.name} pode delegar. O próprio agente não aparece na lista.</p>
              {options.length === 0 && unknown.length === 0 && <p className="muted" style={{ margin: 0 }}>Não há outros agentes no workspace.</p>}
              {options.map((a) => {
                const err = bad.get(a.id)
                return (
                  <div key={a.id} className="checkbox-row">
                    <input id={`d-${a.id}`} data-delegate type="checkbox" checked={form.delegates.includes(a.id)} aria-invalid={err ? true : undefined}
                      aria-describedby={err ? `d-${a.id}-erro` : undefined} onChange={(e) => toggle('delegates', a.id, e.target.checked)} />
                    <div style={{ minWidth: 0 }}>
                      <label htmlFor={`d-${a.id}`}><span style={{ fontWeight: 500 }}>{a.name}</span>{a.description && <span className="desc">{a.description}</span>}</label>
                      {err && <div id={`d-${a.id}-erro`} className="field__error"><Icon name="alert" size={14} /><span>{err}</span></div>}
                    </div>
                  </div>
                )
              })}
              {unknown.map((id) => (
                <div key={id} className="checkbox-row">
                  <input id={`d-${id}`} data-delegate type="checkbox" checked aria-invalid onChange={(e) => toggle('delegates', id, e.target.checked)} aria-describedby={`d-${id}-erro`} />
                  <div><label htmlFor={`d-${id}`}><span className="mono">{id}</span></label><div id={`d-${id}-erro`} className="field__error"><Icon name="alert" size={14} /><span>Destino não encontrado no workspace.</span></div></div>
                </div>
              ))}
            </fieldset>
          </div>
        </form>

        <aside className="panel" aria-labelledby="h-publicado">
          <div className="panel__head">
            <h2 id="h-publicado">{published ? `Publicado v${published.version}` : 'Publicado'}</h2>
            <Chip icon="lock">Somente leitura</Chip>
          </div>
          <div className="panel__body">
            {!published ? <p className="muted" style={{ margin: 0 }}>Este agente ainda não tem versão publicada. Execuções só usam versões publicadas.</p> : (
              <>
                <p className="muted" style={{ margin: 0 }}>Versão ativa agora. Compare com o rascunho ao lado antes de publicar.</p>
                <dl className="kv" style={{ gridTemplateColumns: '110px minmax(0,1fr)' }}>
                  <dt>Nome</dt><dd>{published.name}</dd>
                  <dt>Descrição</dt><dd>{published.description || '—'}</dd>
                  <dt>Tipo de papel</dt><dd>{KIND_LABEL[published.kind] ?? published.kind}</dd>
                  <dt>Modelo</dt><dd className="mono">{published.model}</dd>
                  <dt>Instruções de prompt</dt><dd style={{ whiteSpace: 'pre-wrap', maxHeight: 220, overflow: 'auto' }}>{published.instructions}</dd>
                  <dt>Limites</dt><dd>{fmtLimits(published.limits)}</dd>
                  <dt>Ferramentas</dt><dd><Tags items={published.tools} /></dd>
                  <dt>Agentes delegáveis</dt><dd>{published.delegates.length ? published.delegates.map((d) => names[d] ?? d).join(', ') : 'Nenhum'}</dd>
                </dl>
              </>
            )}
          </div>
        </aside>
      </div>

      <section className="panel" aria-labelledby="h-vinculos">
        <div className="panel__head"><h2 id="h-vinculos">Vínculos diretos</h2></div>
        <div className="panel__body">
          {rel.problem && <ProblemPanel problem={rel.problem} what="os vínculos" onRetry={rel.reload} />}
          <div className="links">
            <div className="links__col">
              <strong className="small">Entrada</strong>
              {inbound.length === 0 ? <span className="muted">Ninguém delega para este agente.</span> : inbound.map((r) => (
                <div key={r.id} className="links__node"><div style={{ fontWeight: 500 }}>{r.name ?? r.id}</div><div className="small muted">{r.version ? `publicado v${r.version}` : 'rascunho'}</div></div>
              ))}
            </div>
            <div className="links__col"><div className="links__node links__node--self"><div style={{ fontWeight: 600 }}>{form.name || baseline.name}</div><div className="small muted">rascunho rev. {baseline.revision}</div><div className="small mono">{baseline.id}</div></div></div>
            <div className="links__col">
              <strong className="small">Saída (rascunho)</strong>
              {form.delegates.length === 0 ? <span className="muted">Não delega para ninguém.</span> : form.delegates.map((d) => (
                <div key={d} className={`links__node${bad.has(d) ? ' links__node--bad' : ''}`}>
                  <div style={{ fontWeight: 500 }}>{names[d] ?? d}</div>
                  {bad.has(d) && <Chip tone="danger" icon="alert">cria ciclo</Chip>}
                </div>
              ))}
              <span className="small muted">{published ? `Publicado v${published.version}: ${published.delegates.length ? published.delegates.map((d) => names[d] ?? d).join(', ') : 'nenhum destino'}` : 'Ainda não publicado'}</span>
            </div>
          </div>
          <div>
            <h3 style={{ margin: '0 0 4px', fontSize: 14 }}>Alternativa textual</h3>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              <li>Recebe delegações de: {inbound.length ? inbound.map((r) => `${r.name ?? r.id} (${r.version ? `publicado v${r.version}` : 'rascunho'})`).join(', ') : 'ninguém'}.</li>
              <li>Delega para no rascunho: {form.delegates.length ? form.delegates.map((d) => `${names[d] ?? d}${bad.has(d) ? ' (inválido, cria ciclo)' : ''}`).join(', ') : 'ninguém'}.</li>
              <li>Delega para no publicado{published ? ` v${published.version}` : ''}: {published?.delegates.length ? published.delegates.map((d) => names[d] ?? d).join(', ') : 'ninguém'}.</li>
            </ul>
          </div>
        </div>
      </section>

      <footer className="small muted" style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', justifyContent: 'space-between' }}>
        <div>
          {published ? `Publicado v${published.version} por ${published.created_by ?? 'autor não informado'} em ${fmtDateTimeZone(published.created_at)} · ` : ''}
          <Link to={`/agentes/${baseline.id}/versoes`}>Ver histórico de versões</Link>
        </div>
        <div>O servidor revalida tudo ao publicar.</div>
      </footer>

      {publishing && <PublishDialog name={baseline.name} next={nextVersion} current={published?.version} changes={publishing.changes} error={publishing.error}
        names={names} busy={busy === 'publish'} returnTo={publishBtn} onClose={() => setPublishing(null)} onConfirm={(s) => void doPublish(s)} />}
      {conflict && (
        <ConflictDialog base={saved} mine={form} current={conflict} names={names} onClose={() => setConflict(null)}
          onReload={() => { setBaseline(conflict); setForm(toForm(conflict)); setErrors({}); setValidation(null); setConflict(null); toast(`Rascunho recarregado na rev. ${conflict.revision}`) }}
          onKeepMine={() => { setBaseline(conflict); setConflict(null); toast(`Base atualizada para a rev. ${conflict.revision}. Salve para sobrescrever com a sua edição.`) }} />
      )}
    </>
  )
}

function PublishDialog({ name, next, current, changes, error, names, busy, returnTo, onClose, onConfirm }: {
  returnTo: RefObject<HTMLElement | null>; name: string; next: number; current?: number; changes: Change[]; error?: string; names: Record<string, string>; busy: boolean
  onClose: () => void; onConfirm: (summary: string) => void
}) {
  const [reviewed, setReviewed] = useState(false)
  const [summary, setSummary] = useState('')
  const access = changes.filter((c) => c.field === 'tools' || c.field === 'delegates')
  const grants = access.some((c) => (c.added?.length ?? 0) > 0)
  const first = current === undefined
  return (
    <Modal title={`Publicar ${name} v${next}?`} wide onClose={onClose} returnFocusTo={returnTo}
      description={first ? 'Primeira publicação: o agente passa a valer para execuções novas.' : `Execuções novas usarão a v${next}. Execuções em andamento continuam com a v${current}.`}>
      <section aria-labelledby="dlg-mudancas">
        <h3 id="dlg-mudancas" style={{ margin: '0 0 8px', fontSize: 14 }}>{first ? 'Conteúdo a publicar' : `Mudanças em relação à v${current}`}</h3>
        <div style={{ maxHeight: 280, overflow: 'auto' }}><DiffView changes={changes} names={names} /></div>
      </section>
      {access.length > 0 && <Note tone="warning" icon="alert">Esta publicação altera permissões ou vínculos do agente.</Note>}
      {grants && (
        <div className="checkbox-row">
          <input id="dlg-revisei" type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} required aria-required="true" aria-describedby="dlg-revisei-ajuda" />
          <label htmlFor="dlg-revisei"><span>Revisei as novas permissões e vínculos</span><span id="dlg-revisei-ajuda" className="desc">Obrigatório para publicar.</span></label>
        </div>
      )}
      <TextField label="Resumo da mudança (opcional)" value={summary} maxLength={500} onChange={(e) => setSummary(e.target.value)} hint="Aparece no histórico de versões." />
      {error && <Note tone="danger" icon="alert">{error}</Note>}
      <p className="small muted" style={{ margin: 0 }}><Icon name="info" size={14} /> O servidor valida novamente o grafo ao publicar.</p>
      <div className="dialog__actions">
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="primary" busy={busy} disabled={grants && !reviewed} onClick={() => onConfirm(summary)}>Publicar v{next}</Button>
      </div>
    </Modal>
  )
}

function ConflictDialog({ base, mine, current, names, onClose, onReload, onKeepMine }: {
  base: AgentContent; mine: AgentContent; current: AgentFull; names: Record<string, string>
  onClose: () => void; onReload: () => void; onKeepMine: () => void
}) {
  const toast = useToast()
  const copy = async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(mine, null, 2)); toast('Sua edição foi copiada.') }
    catch { toast('Não foi possível copiar. Selecione o texto no formulário e copie manualmente.', 'danger') }
  }
  return (
    <Modal wide title="Este rascunho mudou enquanto você editava" onClose={onClose}
      description={`A rev. ${current.revision} foi salva${current.updated_by ? ` por ${current.updated_by}` : ''} às ${fmtTime(current.updated_at)}. Sua edição partiu de outra revisão e continua no formulário; nada foi perdido.`}>
      <Chip tone="warning" icon="alert">Conflito de revisão (412)</Chip>
      <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
        <section aria-labelledby="c-mine"><h3 id="c-mine" style={{ margin: '0 0 8px', fontSize: 14 }}>Sua edição</h3><DiffView changes={diffContent(base, mine)} names={names} /></section>
        <section aria-labelledby="c-theirs"><h3 id="c-theirs" style={{ margin: '0 0 8px', fontSize: 14 }}>Rev. {current.revision} (atual)</h3><DiffView changes={diffContent(base, toForm(current))} names={names} /></section>
      </div>
      <p className="small muted" style={{ margin: 0 }}>+ linha adicionada · − linha removida, ambas em relação ao rascunho que você abriu.</p>
      <div className="dialog__actions">
        <Button icon="copy" onClick={() => void copy()}>Copiar minha edição</Button>
        <Button onClick={onKeepMine}>Manter minha edição sobre a rev. {current.revision}</Button>
        <Button variant="primary" icon="refresh" onClick={onReload}>Recarregar rev. {current.revision}</Button>
      </div>
      <p className="small muted" style={{ margin: 0 }}>Recarregar descarta a edição local: copie antes se quiser guardá-la.</p>
    </Modal>
  )
}
