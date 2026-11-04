import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiProblem, readPage, toProblem } from '../../shared/api'
import { useDocumentTitle, useMediaQuery, useOnline, useResource } from '../../shared/hooks'
import { Icon } from '../../shared/icons'
import { Modal } from '../../shared/modal'
import { Button, ButtonLink, DemoChip, ProblemPanel, StatePanel } from '../../shared/ui'
import { MenuButton } from '../../app/Shell'
import { useConversations } from '../../app/conversations'
import { Composer } from './Composer'
import { saveDraft } from './drafts'
import { readEvidence } from './evidence'
import { AssistantBubble, UserBubble } from './MessageView'
import { buildSteps, byline, isTerminalStatus, phaseOf, PHASE_LIVE_TEXT, readRunInfo, type Phase } from './runs'
import { ScrollArea } from './ScrollArea'
import { SidePanel, type Tab } from './SidePanel'
import type { ChatMessage, Conversation, StartedRun } from './types'
import { useRunStream } from './useRunStream'
import './chat.css'

const DEMO_USER = (import.meta.env.VITE_DEMO_USER_ID as string | undefined) ?? 'cliente1988'

// crypto.randomUUID só existe em contexto seguro (https ou localhost); em http puro, monta o UUID v4 com getRandomValues
function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const b = crypto.getRandomValues(new Uint8Array(16))
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}
const titleFrom = (text: string) => {
  const line = text.split('\n')[0].trim()
  return line.length > 60 ? `${line.slice(0, 57)}…` : line || 'Nova conversa'
}
const statusPhase = (s?: string): Phase => (s === 'failed' || s === 'cancelled' || s === 'interrupted' ? s : 'completed')
const isLivePhase = (p: Phase) => p === 'waiting' || p === 'routing' || p === 'running' || p === 'generating'

interface Sent { id: string; conversationId: string; text: string }

/** Avisa sem alarmar quando a resposta demora; o key = último seq reinicia a contagem a cada evento. */
function SlowHint({ onOpen }: { onOpen: () => void }) {
  const [slow, setSlow] = useState(false)
  useEffect(() => { const t = setTimeout(() => setSlow(true), 30_000); return () => clearTimeout(t) }, [])
  if (!slow) return null
  return (
    <div className="note note--warning" role="status">
      <Icon name="clock" size={14} />
      <span>A resposta está demorando mais que o esperado. A execução continua; acompanhe pelo andamento. <Button variant="bare" onClick={onOpen}>Ver andamento</Button></span>
    </div>
  )
}

export default function ChatPage() {
  const { conversationId } = useParams()
  const navigate = useNavigate()
  const online = useOnline()
  const panelInline = useMediaQuery('(min-width: 1280px)')
  const phone = useMediaQuery('(max-width: 760px)')

  /* ----- lista de conversas (na barra lateral do shell) ----- */
  const { list, setLive } = useConversations()

  /* ----- mensagens da conversa aberta ----- */
  const messagesRes = useResource(conversationId ? `msgs:${conversationId}` : null, async (signal) =>
    readPage<ChatMessage>(await api(`/conversations/${conversationId}/messages`, { signal })).items)
  const messages = messagesRes.data ?? []
  const convRef = useRef(conversationId)
  useLayoutEffect(() => { convRef.current = conversationId })

  const [created, setCreated] = useState<Conversation | null>(null)
  const known = list.items.find((c) => c.id === conversationId) ?? (created?.id === conversationId ? created : undefined)
  const title = conversationId ? known?.title ?? 'Conversa' : 'Nova conversa'
  useDocumentTitle(title)

  /* ----- execução acompanhada nesta conversa ----- */
  const [sent, setSent] = useState<Sent | null>(null)
  const sentHere = sent && sent.conversationId === conversationId ? sent : null
  const last = messages[messages.length - 1]
  const candidate = !sentHere && last && (last.role === 'user' || last.status === 'running' || last.status === 'queued') ? last.run_id : undefined
  const discovered = useResource(candidate ? `run:${candidate}` : null, async (signal) => readRunInfo(await api(`/runs/${candidate}`, { signal })))
  const tracked = sentHere?.id ?? (candidate && discovered.data && !isTerminalStatus(discovered.data.status) ? candidate : null)

  const reloadAfterRun = useCallback(async (runId: string) => {
    const convId = convRef.current
    if (convId) {
      // o assistente é persistido logo depois do evento terminal: tenta algumas vezes
      for (let i = 0; i < 4; i++) {
        try {
          const rows = readPage<ChatMessage>(await api(`/conversations/${convId}/messages`)).items
          if (convRef.current === convId) messagesRes.setData(rows)
          if (rows.some((m) => m.role === 'assistant' && m.run_id === runId)) break
        } catch { break }
        await new Promise((r) => setTimeout(r, 700))
      }
    }
    list.reload()
  }, [messagesRes, list])
  const live = useRunStream(tracked, true, reloadAfterRun)
  const livePhase = phaseOf(live.state, live.info?.status)
  const busy = tracked !== null && isLivePhase(livePhase)
  const listStatus = tracked ? (livePhase === 'waiting' || livePhase === 'routing' || livePhase === 'running' || livePhase === 'generating' ? 'running' : livePhase) : undefined
  useEffect(() => { setLive({ id: conversationId, status: listStatus }) }, [conversationId, listStatus, setLive])
  const [stopping, setStopping] = useState<string | null>(null)

  /* ----- painel Andamento / Fontes ----- */
  const [picked, setPicked] = useState<{ conversationId?: string; runId: string } | null>(null)
  const pickedId = picked && picked.conversationId === conversationId ? picked.runId : null
  const latestRunId = [...messages].reverse().find((m) => m.run_id)?.run_id ?? null
  const inspectId = pickedId ?? tracked ?? latestRunId
  const [panelPref, setPanelPref] = useState<boolean | null>(null)
  const panelOpen = panelPref ?? panelInline
  const [tab, setTab] = useState<Tab>('andamento')
  const [highlight, setHighlight] = useState<string | null>(null)
  const other = useRunStream(inspectId !== tracked ? inspectId : null, panelOpen)
  const panelRun = inspectId !== null && inspectId === tracked ? live : inspectId !== null ? other : null
  const panelDone = panelRun !== null && (panelRun.state.terminal !== null || isTerminalStatus(panelRun.info?.status))
  const evidenceRes = useResource(panelOpen && panelRun?.runId && panelDone ? `ev:${panelRun.runId}` : null, async (signal) => {
    try { return readEvidence(await api(`/runs/${panelRun?.runId}/evidence`, { signal })) }
    catch (e) { if (e instanceof ApiProblem && e.status === 404) return readEvidence({}); throw e }
  })
  const evidence = { data: evidenceRes.data, loading: evidenceRes.loading, problem: evidenceRes.problem, reload: evidenceRes.reload }
  const openPanel = (runId: string | null, t: Tab, marker: string | null = null) => {
    if (runId) setPicked({ conversationId, runId })
    setTab(t); setHighlight(marker); setPanelPref(true)
  }

  /* ----- envio ----- */
  const intent = useRef<{ key: string; text: string; conversationId?: string } | null>(null)
  const [sendError, setSendError] = useState<{ problem: ApiProblem; text: string } | null>(null)
  const [nonce, setNonce] = useState(0)

  const send = async (text: string, fresh = false): Promise<boolean> => {
    setSendError(null)
    let convId = conversationId
    let userId = DEMO_USER
    try {
      if (!convId) {
        const c = await api<Conversation>('/conversations', { method: 'POST', body: { title: titleFrom(text) } })
        convId = c.id
        setCreated(c)
        saveDraft(c.id, text) // o campo remonta na nova URL: o rascunho acompanha até a execução ser aceita
        saveDraft(undefined, '')
        userId = c.user_id ?? userId
      } else userId = list.items.find((c) => c.id === convId)?.user_id ?? userId
      // mesma chave só no retry da mesma intenção (mesmo texto, mesma conversa)
      const prev = intent.current
      const key = !fresh && prev && prev.text === text && prev.conversationId === convId ? prev.key : newIdempotencyKey()
      intent.current = { key, text, conversationId: convId }
      const run = await api<StartedRun>(`/conversations/${convId}/runs`, {
        method: 'POST', body: { message: text, user_id: userId }, headers: { 'Idempotency-Key': key },
      })
      intent.current = null
      saveDraft(convId, '')
      setSent({ id: run.run_id, conversationId: convId, text })
      setPicked(null); setStopping(null); setNonce((n) => n + 1)
      if (convId !== conversationId) navigate(`/conversas/${convId}`, { replace: true })
      else messagesRes.reload()
      list.reload()
      return true
    } catch (e) {
      setSendError({ problem: toProblem(e), text })
      if (convId && convId !== conversationId) navigate(`/conversas/${convId}`, { replace: true })
      return false
    }
  }

  const stop = async () => {
    if (!tracked) return
    setStopping(tracked)
    try { await api(`/runs/${tracked}/cancel`, { method: 'POST' }) }
    catch (e) { setStopping(null); setSendError({ problem: toProblem(e), text: '' }) }
  }
  const resendFor = (m: ChatMessage) => {
    const i = messages.indexOf(m)
    const prompt = [...messages.slice(0, i)].reverse().find((x) => x.role === 'user')
    return prompt ? () => void send(prompt.content, true) : undefined
  }

  /* ----- renderização ----- */
  const revoked = messagesRes.problem?.status === 403 && messagesRes.data !== undefined
  const liveText = live.state.text || (live.state.terminal ? live.info?.text ?? '' : '')
  const showLive = tracked !== null && !messages.some((m) => m.role === 'assistant' && m.run_id === tracked)
  const showOptimistic = sentHere !== null && !messages.some((m) => m.role === 'user' && m.run_id === sentHere.id)
  const steps = tracked ? buildSteps(live.state, livePhase) : []
  const announce = tracked ? (isLivePhase(livePhase) ? steps.find((s) => s.status === 'running')?.title ?? PHASE_LIVE_TEXT[livePhase] : PHASE_LIVE_TEXT[livePhase]) : ''
  const panelByline = panelRun ? byline(panelRun.state.events) : undefined
  const sendBusy = busy || (sentHere !== null && messagesRes.loading)

  let body: ReactNode
  if (conversationId && messagesRes.problem && messagesRes.data === undefined) {
    body = messagesRes.problem.status === 404
      ? <StatePanel kind="empty" title="Conversa não encontrada" message="Ela pode ter sido removida ou você não tem acesso." actions={<ButtonLink to="/conversas" icon="plus">Nova conversa</ButtonLink>} />
      : <ProblemPanel problem={messagesRes.problem} what="as mensagens" onRetry={messagesRes.reload} />
  } else if (revoked) {
    body = <StatePanel kind="revoked" message="Seu acesso a esta conversa foi removido durante o uso. O conteúdo foi ocultado." />
  } else if (conversationId && messagesRes.loading && messagesRes.data === undefined && !sentHere) {
    body = <StatePanel kind="loading" what="mensagens" />
  } else if (!conversationId && !sentHere) {
    body = <StatePanel kind="empty" title="Nova conversa" message="Escreva sua pergunta abaixo. As respostas mostram as fontes usadas e o andamento do atendimento." />
  } else {
    body = (
      <>
        {messages.map((m) => m.role === 'user'
          ? <UserBubble key={m.id} text={m.content} at={m.created_at} />
          : <AssistantBubble key={m.id} phase={statusPhase(m.status)} text={m.content} at={m.created_at} runId={m.run_id}
              byline={m.agent_name ?? (m.run_id && m.run_id === panelRun?.runId ? panelByline : undefined)}
              requestId={m.request_id ?? (m.run_id === tracked ? live.state.failure?.requestId : undefined)}
              failureMessage={m.error?.message ?? (m.run_id === tracked ? live.state.failure?.message : undefined)}
              actionsDisabled={busy || !online}
              onCite={(runId, marker) => openPanel(runId, 'fontes', marker)} onSources={(runId) => openPanel(runId, 'fontes')} onResend={resendFor(m)} />)}
        {showOptimistic && sentHere && <UserBubble text={sentHere.text} />}
        {showLive && tracked && (
          <AssistantBubble phase={livePhase} text={liveText} runId={tracked} streaming byline={byline(live.state.events)}
            requestId={live.state.failure?.requestId} failureMessage={live.state.failure?.message} actionsDisabled={busy || !online}
            onCite={(runId, marker) => openPanel(runId, 'fontes', marker)} onSources={(runId) => openPanel(runId, 'fontes')}
            onResend={sentHere ? () => void send(sentHere.text, true) : undefined}
            slowHint={isLivePhase(livePhase) && !live.reconnecting ? <SlowHint key={live.state.lastSeq} onOpen={() => openPanel(tracked, 'andamento')} /> : null} />
        )}
      </>
    )
  }

  const panel = (embedded: boolean) => (
    <SidePanel run={panelRun} evidence={evidence} tab={tab} onTab={(t) => { setTab(t); setHighlight(null) }} onClose={() => setPanelPref(false)} highlight={highlight} embedded={embedded} />
  )

  return (
    <div className={`chat${panelOpen && panelInline ? ' chat--panel' : ''}`}>
      <main id="main" tabIndex={-1} className="chat__main" aria-labelledby="chat-title">
        <header className="chat__head">
          <div className="head-title">
            <MenuButton />
            <h1 id="chat-title" className="chat__title" title={title}>{title}</h1>
            <DemoChip />
          </div>
          <Button icon="clock" aria-expanded={panelOpen} aria-controls="painel-lateral" onClick={() => setPanelPref(!panelOpen)}>Andamento</Button>
        </header>
        {live.reconnecting && tracked && (
          <div role="status" className="banner" style={{ gap: 8 }}>
            <Icon name="wifiOff" size={14} /><span>Reconectando… O texto recebido foi mantido.</span>
            <Button small onClick={live.retryNow}>Tentar agora</Button>
          </div>
        )}
        <ScrollArea version={`${messages.length}|${liveText.length}|${livePhase}|${showOptimistic}|${live.reconnecting}|${sendError ? 1 : 0}`} nonce={nonce}>
          <div className="chat__thread"><div className="demo-row"><DemoChip /></div>{body}</div>
        </ScrollArea>
        {sendError && (
          <div role="alert" className="banner banner--danger chat__error">
            <Icon name="alert" size={14} />
            <span>{sendError.text ? 'Não foi possível enviar a mensagem.' : 'Não foi possível parar a execução.'} {sendError.problem.message}
              {sendError.problem.requestId && <> request_id <code>{sendError.problem.requestId}</code></>}</span>
            {sendError.text && sendError.problem.status !== 409 && <Button small icon="refresh" onClick={() => void send(sendError.text)}>Tentar novamente</Button>}
            <Button small variant="link" onClick={() => setSendError(null)}>Dispensar</Button>
          </div>
        )}
        <Composer key={conversationId ?? 'new'} conversationId={conversationId} busy={sendBusy && tracked !== null} stopping={stopping === tracked && tracked !== null}
          offline={!online} onSend={(t) => send(t)} onStop={() => void stop()} />
      </main>
      {panelOpen && panelInline && <aside id="painel-lateral" aria-label="Andamento e fontes" className="chat__panel">{panel(false)}</aside>}
      {panelOpen && !panelInline && (
        <Modal variant={phone ? 'bottom' : 'right'} title="Andamento e fontes" hideTitle onClose={() => setPanelPref(false)}>{panel(true)}</Modal>
      )}
      <div className="sr-only" role="status" aria-live="polite">{announce}</div>
    </div>
  )
}
