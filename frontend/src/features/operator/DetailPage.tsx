import { useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiProblem, toProblem } from '../../shared/api'
import { fmtDate, fmtDateTime, fmtTime } from '../../shared/format'
import { useDocumentTitle, usePolling, useResource } from '../../shared/hooks'
import { Icon } from '../../shared/icons'
import { Modal } from '../../shared/modal'
import { useToast } from '../../shared/toast-context'
import { Markdown } from '../../shared/markdown'
import { Button, Chip, Note, ProblemPanel, StatePanel, TextAreaField } from '../../shared/ui'
import { author, claimHandoff, humanize, detailMode, elapsed, reasonLabel, STATUS_LABEL, timelineText, validateResolution, POLL_MS, type Detail } from './model'
import { opApi, useOperatorSession } from './session'

const brl = (v: string) => { const n = Number(v); return Number.isFinite(n) ? new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n) : v }

/** Campo de texto + botão; limpa só quando o envio dá certo. */
function TextForm({ label, submitLabel, hint, onSubmit }: { label: string; submitLabel: string; hint?: string; onSubmit: (text: string) => Promise<boolean> }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!text.trim() || busy) return
    setBusy(true)
    if (await onSubmit(text.trim())) setText('')
    setBusy(false)
  }
  return (
    <form className="op-form" onSubmit={(e) => void submit(e)}>
      <TextAreaField label={label} hint={hint} rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      <div><Button type="submit" variant="primary" busy={busy} disabled={!text.trim()}>{submitLabel}</Button></div>
    </form>
  )
}

/** Nota de resolução obrigatória: o botão de confirmar só habilita com texto. */
export function CloseForm({ onSubmit, onCancel }: { onSubmit: (resolution: string) => Promise<string | undefined>; onCancel: () => void }) {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | undefined>()
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const invalid = validateResolution(text)
    if (invalid) { setError(invalid); ref.current?.focus(); return }
    setBusy(true)
    const err = await onSubmit(text.trim())
    setBusy(false)
    if (err) { setError(err); ref.current?.focus() }
  }
  return (
    <form className="op-form" onSubmit={(e) => void submit(e)} noValidate>
      <TextAreaField ref={ref} label="Nota de resolução" hint="Obrigatória. Fica na trilha do protocolo; o cliente não vê." rows={4}
        value={text} error={error} required onChange={(e) => { setText(e.target.value); setError(undefined) }} />
      <div className="dialog__actions">
        <Button onClick={onCancel}>Cancelar</Button>
        <Button type="submit" variant="primary" busy={busy} disabled={!text.trim()}>Encerrar atendimento</Button>
      </div>
    </form>
  )
}

function Section({ id, title, extra, children }: { id: string; title: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel" aria-labelledby={id}>
      <div className="panel__head"><h2 id={id}>{title}</h2>{extra}</div>
      <div className="panel__body">{children}</div>
    </section>
  )
}

interface ViewProps {
  detail: Detail
  meId?: string
  conflict?: string | null
  claiming?: boolean
  onClaim: () => void
  onSend: (text: string) => Promise<boolean>
  onNote: (text: string) => Promise<boolean>
  onClose: () => void
  now?: number
}

export function DetailView({ detail, meId, conflict, claiming, onClaim, onSend, onNote, onClose, now }: ViewProps) {
  const t = detail.handoff
  const mode = detailMode(t, meId, conflict)
  const timeline = t.timeline ?? []
  const notes = timeline.filter((e) => e.kind === 'note')
  const holder = t.operator?.name ?? conflict
  return (
    <>
      <div className="page__head">
        <div>
          <nav aria-label="Trilha de navegação" className="crumbs"><ol><li><Link to="..">Protocolos</Link></li><li aria-hidden="true">/</li><li aria-current="page">{t.protocol}</li></ol></nav>
          <h1 id="op-detail-t">Protocolo <span className="mono">{t.protocol}</span></h1>
          <p>
            <Chip tone={t.status === 'aberto' ? 'warning' : t.status === 'em_atendimento' ? 'progress' : 'success'} icon={t.status === 'encerrado' ? 'check' : 'clock'}>{STATUS_LABEL[t.status]}</Chip>
            {' '}{reasonLabel(t.reason)} · {t.status === 'encerrado' ? `duração ${elapsed(t, now)}` : `aberto há ${elapsed(t, now)}`}
            {holder ? ` · assumido por ${holder}` : ''}
          </p>
        </div>
        <div className="page__actions">
          {mode === 'claim' && <Button variant="primary" icon="chat" busy={claiming} onClick={onClaim}>Assumir conversa</Button>}
          {mode === 'owner' && <Button variant="danger" icon="check" onClick={onClose}>Encerrar atendimento</Button>}
        </div>
      </div>

      {conflict && <Note tone="warning" icon="lock">{conflict} já assumiu este protocolo. Você está vendo em modo somente leitura.</Note>}
      {!conflict && mode === 'readonly' && t.status === 'em_atendimento' && <Note icon="lock">Em atendimento por {t.operator?.name ?? 'outro atendente'}. Somente leitura.</Note>}
      {t.status === 'encerrado' && <Note icon="check">Encerrado {t.closed_at ? `em ${fmtDateTime(t.closed_at)}` : ''}{t.resolution ? `. Resolução: ${t.resolution}` : ''}</Note>}

      <div className="op-detail">
        <div className="op-detail__main">
          <Section id="op-resumo" title="Resumo do assistente">
            <p className="op-pre">{t.summary || 'O assistente não deixou resumo.'}</p>
            <p className="small muted" style={{ margin: 0 }}>Motivo: {reasonLabel(t.reason)} · Cliente: {t.user_id}</p>
          </Section>
          <Section id="op-conversa" title="Conversa">
            {detail.messages.length === 0
              ? <StatePanel kind="empty" title="Sem mensagens" message="Este protocolo não tem conversa vinculada." />
              : (
                <ol className="op-msgs">
                  {detail.messages.map((m) => m.role === 'system'
                    ? <li key={m.id} className="op-msg op-msg--system"><Icon name="info" size={14} /><span>{m.content}</span></li>
                    : (
                      <li key={m.id} className={`op-msg op-msg--${m.role}`}>
                        <div className="small"><strong>{author(m)}</strong>{m.created_at ? <span className="muted"> · {fmtTime(m.created_at)}</span> : null}</div>
                        {m.role === 'assistant' ? <Markdown text={m.content} /> : <div className="op-pre">{m.content}</div>}
                      </li>
                    ))}
                </ol>
              )}
            {mode === 'owner' && <TextForm label="Mensagem para o cliente" submitLabel="Enviar mensagem" hint="O cliente vê com o selo de atendente e o seu nome." onSubmit={onSend} />}
          </Section>
        </div>
        <div className="op-detail__side">
          <Section id="op-notas" title="Notas internas">
            {notes.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma nota ainda.</p> : (
              <ul className="op-list">{notes.map((n, i) => <li key={`${n.at}-${i}`}><div className="small muted">{n.operator?.name ?? 'Atendente'} · {fmtDateTime(n.at)}</div><div className="op-pre">{n.content}</div></li>)}</ul>
            )}
            <TextForm label="Nova nota" submitLabel="Adicionar nota" hint="Só atendentes veem as notas." onSubmit={onNote} />
          </Section>
          <Section id="op-terminais" title="Terminais">
            {detail.customer.terminals.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhum terminal.</p> : (
              <ul className="op-list">{detail.customer.terminals.map((m) => (
                <li key={m.serial_suffix}><strong>{m.model}</strong>{m.model.includes(m.serial_suffix) ? '' : ` · final ${m.serial_suffix}`}<div className="small muted">{m.connectivity}{m.error ? ` · erro: ${m.error}` : ' · sem erro'}</div></li>
              ))}</ul>
            )}
          </Section>
          <Section id="op-recebiveis" title="Recebíveis">
            {detail.customer.receivables.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhum recebível.</p> : (
              <div className="table-wrap">
                <table className="table">
                  <caption className="sr-only">Recebíveis do cliente</caption>
                  <thead><tr><th scope="col">Venda</th><th scope="col">Previsão</th><th scope="col">Valor</th><th scope="col">Situação</th></tr></thead>
                  <tbody>{detail.customer.receivables.map((r, i) => (
                    <tr key={i}><td>{fmtDate(r.sale_date)}</td><td>{fmtDate(r.due_date)}</td><td>{brl(r.amount)}<div className="small muted">{humanize(r.payment_type)}</div></td><td>{humanize(r.status)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </Section>
          <Section id="op-trilha" title="Trilha de auditoria">
            <ol className="op-list">{timeline.map((e, i) => (
              <li key={`${e.at}-${i}`}><div>{timelineText(e)}</div><div className="small muted">{fmtDateTime(e.at)}{e.kind === 'closed' && e.resolution ? ` · ${e.resolution}` : ''}</div></li>
            ))}</ol>
          </Section>
        </div>
      </div>
    </>
  )
}

export default function DetailPage() {
  const { protocol = '' } = useParams()
  useDocumentTitle(`Protocolo ${protocol} · Área do atendente`)
  const session = useOperatorSession()
  const toast = useToast()
  const path = `/handoffs/${encodeURIComponent(protocol)}`
  const res = useResource(`op-detail:${protocol}`, (signal) => opApi<Detail>(path, { signal }))
  const closed = res.data?.handoff.status === 'encerrado'
  usePolling(res.reload, POLL_MS, !closed)
  const [conflict, setConflict] = useState<string | null>(null)
  const [claiming, setClaiming] = useState(false)
  const [closing, setClosing] = useState(false)
  const [actionError, setActionError] = useState<ApiProblem | null>(null)

  const act = async (fn: () => Promise<unknown>, ok: string): Promise<boolean> => {
    setActionError(null)
    try { await fn(); toast(ok); res.reload(); return true }
    catch (e) { setActionError(toProblem(e)); res.reload(); return false }
  }
  const claim = async () => {
    setClaiming(true); setActionError(null)
    try {
      const r = await claimHandoff(protocol)
      if ('conflict' in r) { setConflict(r.conflict); toast(`${r.conflict} já assumiu este protocolo.`, 'danger') }
      else toast('Você assumiu a conversa. O cliente foi avisado.')
    } catch (e) { setActionError(toProblem(e)) }
    setClaiming(false)
    res.reload()
  }
  const close = async (resolution: string): Promise<string | undefined> => {
    try {
      await opApi(`${path}/close`, { method: 'POST', body: { resolution } })
      setClosing(false); toast('Atendimento encerrado. O cliente voltou ao assistente.'); res.reload()
      return undefined
    } catch (e) {
      const p = toProblem(e)
      if (p.status === 422) return 'Escreva a nota de resolução para encerrar.'
      setClosing(false); setActionError(p); res.reload()
      return undefined
    }
  }

  let body: ReactNode
  const srTitle = <h1 id="op-detail-t" className="sr-only">Protocolo {protocol}</h1>
  if (res.problem && !res.data) {
    body = <>{srTitle}{res.problem.status === 404
      ? <StatePanel kind="empty" title="Protocolo não encontrado" message="Confira o número ou volte para a lista." actions={<Link to="..">Voltar para Protocolos</Link>} />
      : <ProblemPanel problem={res.problem} what="o protocolo" onRetry={res.reload} />}</>
  } else if (!res.data) {
    body = <>{srTitle}<StatePanel kind="loading" what="o protocolo" /></>
  } else {
    body = (
      <>
        {actionError && (
          <div role="alert" className="alert-box" style={{ alignItems: 'center' }}>
            <Icon name="alert" />
            <span>{actionError.message}{actionError.requestId && <> request_id <code>{actionError.requestId}</code></>}</span>
            <Button small variant="link" onClick={() => setActionError(null)}>Dispensar</Button>
          </div>
        )}
        <DetailView detail={res.data} meId={session?.operator.id} conflict={conflict} claiming={claiming} onClaim={() => void claim()}
          onSend={(content) => act(() => opApi(`${path}/messages`, { method: 'POST', body: { content } }), 'Mensagem enviada ao cliente.')}
          onNote={(content) => act(() => opApi(`${path}/notes`, { method: 'POST', body: { content } }), 'Nota adicionada.')}
          onClose={() => setClosing(true)} />
      </>
    )
  }
  return (
    <main id="main" tabIndex={-1} className="page op__page" aria-labelledby="op-detail-t">
      {body}
      {closing && (
        <Modal title="Encerrar atendimento" description="O cliente recebe um aviso de encerramento e volta a falar com o assistente." onClose={() => setClosing(false)}>
          <CloseForm onSubmit={close} onCancel={() => setClosing(false)} />
        </Modal>
      )}
    </main>
  )
}
