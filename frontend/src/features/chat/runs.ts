/** Eventos SSE de uma execução (design.md §3) e tudo que a interface deriva deles. Tolera campos ausentes. */
export interface RunEvent {
  run_id?: string
  seq: number
  occurred_at: string
  type: string
  agent_id?: string
  parent_event_id?: string | null
  payload: Record<string, unknown>
}

export type Terminal = 'completed' | 'failed' | 'cancelled' | 'interrupted'
export type Phase = 'waiting' | 'routing' | 'running' | 'generating' | Terminal
export const TERMINAL_STATUS: readonly string[] = ['completed', 'failed', 'cancelled', 'interrupted']
export const isTerminalStatus = (s?: string): s is Terminal => !!s && TERMINAL_STATUS.includes(s)
export const EVENT_TYPES = [
  'run.started', 'route.selected', 'agent.started', 'agent.completed', 'agent.failed', 'tool.started', 'tool.completed',
  'tool.failed', 'retrieval.completed', 'web.completed', 'response.delta', 'usage.recorded',
  'run.completed', 'run.failed', 'run.cancelled', 'run.interrupted',
] as const

/** Resposta de GET /runs/{id}, lida de forma tolerante. */
export interface RunInfo {
  id: string
  status: string
  conversationId?: string
  graphVersion?: string | number
  traceId?: string
  text: string
  seq?: number
  startedAt?: string
  finishedAt?: string
  durationMs?: number
  usage?: unknown
  costUsd?: number | null
  errorMessage?: string
  errorRequestId?: string
}

const str = (v: unknown) => (typeof v === 'string' && v ? v : undefined)
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)
const rec = (v: unknown) => (v && typeof v === 'object' ? (v as Record<string, unknown>) : {})

export function readRunInfo(raw: unknown): RunInfo {
  const r = rec(raw)
  const err = rec(r.error)
  return {
    id: str(r.id) ?? str(r.run_id) ?? '',
    status: str(r.status) ?? 'running',
    conversationId: str(r.conversation_id),
    graphVersion: (r.graph_version ?? undefined) as string | number | undefined,
    traceId: str(r.trace_id),
    text: str(r.answer) ?? str(r.text) ?? str(r.partial_text) ?? '',
    seq: num(r.seq),
    startedAt: str(r.started_at) ?? str(r.created_at),
    finishedAt: str(r.finished_at) ?? (isTerminalStatus(str(r.status)) ? str(r.updated_at) : undefined),
    durationMs: num(r.duration_ms),
    usage: r.usage,
    costUsd: typeof rec(r.usage).cost_usd === 'number' ? (rec(r.usage).cost_usd as number) : typeof r.cost_usd === 'number' ? r.cost_usd : null,
    errorMessage: str(err.message) ?? str(r.error_message) ?? (typeof r.error === 'string' ? r.error : undefined),
    errorRequestId: str(err.request_id),
  }
}

/* ---------- Estado acumulado a partir dos eventos ---------- */
export interface RunState {
  /** Todos os eventos, exceto response.delta (o texto fica em `text`). */
  events: RunEvent[]
  text: string
  lastSeq: number
  terminal: Terminal | null
  failure?: { message?: string; requestId?: string }
}
export const initialRunState: RunState = { events: [], text: '', lastSeq: 0, terminal: null }

const TERMINAL_EVENT: Record<string, Terminal> = {
  'run.completed': 'completed', 'run.failed': 'failed', 'run.cancelled': 'cancelled', 'run.interrupted': 'interrupted',
}
export const isTerminalEvent = (type: string) => type in TERMINAL_EVENT

/** Replays e reconexões reentregam eventos: o seq garante que cada trecho entra uma vez só. */
export function applyEvents(state: RunState, batch: RunEvent[]): RunState {
  let { text, lastSeq, terminal, failure } = state
  const events = [...state.events]
  for (const ev of batch) {
    if (ev.seq <= lastSeq) continue
    lastSeq = ev.seq
    const p = rec(ev.payload)
    if (ev.type === 'response.delta') { if (typeof p.text === 'string') text += p.text; continue }
    events.push({ ...ev, payload: p })
    if (ev.type in TERMINAL_EVENT) {
      terminal = TERMINAL_EVENT[ev.type]
      if (terminal === 'failed' || terminal === 'interrupted') failure = { message: str(p.message), requestId: str(p.request_id) }
    }
  }
  return { events, text, lastSeq, terminal, failure }
}

export function phaseOf(s: RunState, status?: string): Phase {
  if (s.terminal) return s.terminal
  if (isTerminalStatus(status)) return status
  if (s.text) return 'generating'
  if (s.events.some((e) => e.type === 'route.selected' || e.type.startsWith('tool.'))) return 'running'
  if (s.events.length > 0 || status === 'running') return 'routing'
  return 'waiting'
}

export const PHASE_LABEL: Record<Phase, string> = {
  waiting: 'Aguardando', routing: 'Roteando', running: 'Executando', generating: 'Gerando',
  completed: 'Concluída', failed: 'Falhou', cancelled: 'Cancelada', interrupted: 'Interrompida',
}
export const PHASE_LIVE_TEXT: Record<Phase, string> = {
  waiting: 'Na fila para iniciar', routing: 'Entendendo o pedido…', running: 'Consultando informações…',
  generating: 'Escrevendo a resposta…', completed: 'Resposta concluída.', failed: 'Não foi possível concluir a resposta.',
  cancelled: 'Parado por você · texto parcial', interrupted: 'Execução interrompida · texto parcial',
}

/* ---------- Andamento: passos em linguagem de atendimento ---------- */
export interface Step {
  id: string
  status: 'done' | 'running' | 'error' | 'cancelled' | 'pending'
  title: string
  detail?: string
  demo?: boolean
  sources?: boolean
  requestId?: string
}

const TOOL_COPY: Record<string, { running: string; done: string; failed: string; demo?: boolean }> = {
  get_terminal_status: { running: 'Consultando sua maquininha', done: 'Consultou sua maquininha', failed: 'Não conseguiu consultar sua maquininha', demo: true },
  get_receivables: { running: 'Consultando seus recebíveis', done: 'Consultou seus recebíveis', failed: 'Não conseguiu consultar seus recebíveis', demo: true },
  search_knowledge: { running: 'Buscando orientação na base Getnet', done: 'Buscou orientação na base Getnet', failed: 'Não conseguiu buscar na base Getnet' },
  request_human_handoff: { running: 'Encaminhando para atendimento humano', done: 'Encaminhou para atendimento humano', failed: 'Não conseguiu encaminhar para atendimento humano', demo: true },
  web_search: { running: 'Pesquisando na web', done: 'Pesquisou na web', failed: 'A busca na web não está disponível agora' },
}
/** O backend resume ferramentas como "1 registros"; aqui vira frase de atendimento. */
function friendlySummary(tool: string, summary?: string): string | undefined {
  const n = Number(/^(\d+) registros?$/.exec(summary ?? '')?.[1])
  if (!Number.isNaN(n) && tool === 'get_terminal_status') return `${n} ${n === 1 ? 'maquininha consultada' : 'maquininhas consultadas'}.`
  if (!Number.isNaN(n) && tool === 'get_receivables') return `${n} ${n === 1 ? 'recebível consultado' : 'recebíveis consultados'}.`
  return summary && summary !== 'Concluída' ? summary : undefined
}
const GENERIC_TOOL = { running: 'Consultando uma ferramenta', done: 'Consultou uma ferramenta', failed: 'Uma ferramenta falhou' }

export const agentNames = (events: RunEvent[]): Record<string, string> => {
  const names: Record<string, string> = {}
  for (const e of events) {
    const p = rec(e.payload)
    const id = str(p.agent_id) ?? e.agent_id
    const name = str(p.name) ?? str(p.agent_name)
    if (id && name) names[id] = name
  }
  return names
}

interface StepCtx { citedCount?: number; limitation?: string; names?: Record<string, string> }

export function buildSteps(s: RunState, phase: Phase, ctx: StepCtx = {}): Step[] {
  const names = { ...agentNames(s.events), ...ctx.names }
  const steps: Step[] = []
  const open = new Map<string, Step>() // `${agent}:${tool}` → passo em andamento
  let routes = 0
  const nameOf = (id?: string) => (id ? names[id] ?? 'um especialista' : 'um especialista')

  for (const e of s.events) {
    const p = rec(e.payload)
    const agent = str(p.agent_id) ?? e.agent_id ?? ''
    const tool = str(p.tool) ?? str(p.name) ?? ''
    switch (e.type) {
      case 'route.selected': {
        const dest = str(p.destination_name) ?? nameOf(str(p.destination) ?? str(p.to_agent_id))
        const first = routes++ === 0
        steps.push({
          id: `route-${e.seq}`, status: 'done',
          title: first ? 'Entendeu o pedido' : `Pediu apoio: ${dest}`,
          detail: first ? `${str(p.reason) ?? 'Pedido analisado'}. Encaminhado a ${dest}.` : str(p.reason),
        })
        break
      }
      case 'tool.started': {
        if (tool === 'delegate_to_agent') break
        const c = TOOL_COPY[tool] ?? GENERIC_TOOL
        const step: Step = { id: `tool-${e.seq}`, status: 'running', title: c.running, demo: TOOL_COPY[tool]?.demo }
        steps.push(step)
        open.set(`${agent}:${tool}`, step)
        break
      }
      case 'tool.completed':
      case 'tool.failed': {
        if (tool === 'delegate_to_agent') break
        const c = TOOL_COPY[tool] ?? GENERIC_TOOL
        let step = open.get(`${agent}:${tool}`)
        if (!step) { step = { id: `tool-${e.seq}`, status: 'running', title: '', demo: TOOL_COPY[tool]?.demo }; steps.push(step) }
        open.delete(`${agent}:${tool}`)
        const failed = e.type === 'tool.failed'
        step.status = failed ? 'error' : 'done'
        step.title = failed ? c.failed : c.done
        step.detail ??= failed ? str(p.message) : friendlySummary(tool, str(p.summary))
        if (failed) step.requestId = str(p.request_id)
        break
      }
      case 'retrieval.completed':
      case 'web.completed': {
        const web = e.type === 'web.completed' || p.source === 'web'
        const key = `${agent}:${web ? 'web_search' : 'search_knowledge'}`
        const list = (p.results ?? p.chunks ?? p.sources) as unknown[] | undefined
        const n = num(p.count) ?? (Array.isArray(list) ? list.length : undefined)
        let step = open.get(key) ?? [...steps].reverse().find((x) => x.id.startsWith('tool-') && x.title === (web ? TOOL_COPY.web_search : TOOL_COPY.search_knowledge).done)
        if (!step) { step = { id: `ret-${e.seq}`, status: 'done', title: '' }; steps.push(step) }
        step.title = (web ? TOOL_COPY.web_search : TOOL_COPY.search_knowledge).done
        const found = n === undefined ? undefined : web ? `${n} ${n === 1 ? 'fonte' : 'fontes'} na web` : `${n} ${n === 1 ? 'trecho encontrado' : 'trechos encontrados'}`
        step.detail = [found, !web && ctx.citedCount !== undefined ? `${ctx.citedCount} ${ctx.citedCount === 1 ? 'usado' : 'usados'} na resposta` : undefined].filter(Boolean).join(', ') + (found ? '.' : '')
        step.sources = !web
        break
      }
      case 'agent.failed':
        steps.push({ id: `agent-${e.seq}`, status: 'error', title: `${nameOf(agent)} não conseguiu concluir`, detail: str(p.message), requestId: str(p.request_id) })
        break
    }
  }

  const noRoute = routes === 0
  if (noRoute && !s.terminal && phase !== 'waiting') steps.unshift({ id: 'understanding', status: 'running', title: 'Entendendo o pedido' })

  switch (phase) {
    case 'completed':
      steps.push({ id: 'final', status: 'done', title: 'Resposta pronta', detail: ctx.limitation ? `Com uma limitação: ${ctx.limitation}` : undefined })
      break
    case 'failed':
      steps.push({ id: 'final', status: 'error', title: 'Não foi possível concluir o atendimento', detail: s.failure?.message, requestId: s.failure?.requestId })
      break
    case 'cancelled':
      steps.push({ id: 'final', status: 'cancelled', title: 'Parado por você', detail: 'O texto até aqui foi mantido.' })
      break
    case 'interrupted':
      steps.push({ id: 'final', status: 'error', title: 'Execução interrompida', detail: 'O servidor responsável ficou indisponível. O texto recebido foi mantido. Inicie uma nova execução para continuar.' })
      break
    case 'generating':
      steps.push({ id: 'final', status: 'running', title: 'Escrevendo a resposta', detail: 'Pode parar a qualquer momento.' })
      break
    case 'waiting':
      steps.push({ id: 'final', status: 'pending', title: 'Aguardando início', detail: 'Na fila para iniciar.' })
      break
    default:
      if (!steps.some((x) => x.status === 'running')) steps.push({ id: 'final', status: 'running', title: 'Preparando a resposta' })
  }
  return steps
}

/* ---------- Detalhes técnicos ---------- */
export interface Tech {
  runId: string
  traceId?: string
  startedAt?: string
  durationMs?: number
  inProgress: boolean
  tokens?: { input: number; output: number }
  cost: { usd?: number; partial: boolean; known: boolean }
  agents: { name: string; version?: string | number }[]
  graphVersion?: string | number
  tools: { tool: string; durationMs?: number }[]
  pricingVersion?: string
}

const ms = (iso?: string) => (iso ? Date.parse(iso) : NaN)

export function buildTech(runId: string, s: RunState, info?: RunInfo | null): Tech {
  const names = agentNames(s.events)
  const agents: Tech['agents'] = []
  const toolStarts = new Map<string, number>()
  const tools: Tech['tools'] = []
  let input = 0, output = 0, hasTokens = false
  let costSum = 0, costKnown = 0, costUnknown = 0
  let traceId = info?.traceId
  let pricingVersion: string | undefined
  for (const e of s.events) {
    const p = rec(e.payload)
    traceId ??= str(p.trace_id) ?? str((e as unknown as Record<string, unknown>).trace_id)
    if (e.type === 'agent.started') {
      const id = str(p.agent_id) ?? e.agent_id ?? ''
      if (!agents.some((a) => a.name === (names[id] ?? id))) agents.push({ name: names[id] ?? id, version: (p.version ?? undefined) as string | number | undefined })
    }
    const agent = str(p.agent_id) ?? e.agent_id ?? ''
    const tool = str(p.tool) ?? ''
    if (e.type === 'tool.started') toolStarts.set(`${agent}:${tool}`, ms(e.occurred_at))
    if (e.type === 'tool.completed' || e.type === 'tool.failed') {
      const start = toolStarts.get(`${agent}:${tool}`)
      tools.push({ tool, durationMs: num(p.duration_ms) ?? (start !== undefined && !Number.isNaN(start) ? ms(e.occurred_at) - start : undefined) })
    }
    if (e.type === 'usage.recorded') {
      const u = rec(p.usage)
      const i = num(u.input_tokens) ?? num(u.prompt_tokens)
      const o = num(u.output_tokens) ?? num(u.completion_tokens)
      if (i !== undefined || o !== undefined) { hasTokens = true; input += i ?? 0; output += o ?? 0 }
      if (typeof p.cost_usd === 'number') { costSum += p.cost_usd; costKnown++ } else costUnknown++
      pricingVersion = str(p.pricing_version) ?? pricingVersion
    }
  }
  const ru = rec(info?.usage)
  if (!hasTokens && (num(ru.input_tokens) !== undefined || num(ru.output_tokens) !== undefined)) { hasTokens = true; input = num(ru.input_tokens) ?? 0; output = num(ru.output_tokens) ?? 0 }
  pricingVersion ??= str(ru.pricing_version)
  const started = s.events.find((e) => e.type === 'run.started')?.occurred_at ?? info?.startedAt ?? s.events[0]?.occurred_at
  const last = s.events[s.events.length - 1]
  const end = s.terminal ? last?.occurred_at : undefined
  const duration = info?.durationMs ?? (started && end ? ms(end) - ms(started) : undefined)
  // Preço unitário ausente nunca vira zero: sem nenhum valor informado, o custo é "não informado".
  const cost = costKnown > 0
    ? { usd: costSum, partial: costUnknown > 0, known: true }
    : typeof info?.costUsd === 'number' ? { usd: info.costUsd, partial: false, known: true } : { partial: false, known: false }
  return {
    runId, traceId, startedAt: started, durationMs: Number.isFinite(duration) ? duration : undefined,
    inProgress: !s.terminal && !isTerminalStatus(info?.status),
    tokens: hasTokens ? { input, output } : undefined, cost, agents, graphVersion: info?.graphVersion, tools, pricingVersion,
  }
}

/** Linha de autoria acima da resposta: "Atendimento ao Cliente, com apoio do Especialista Getnet". */
export function byline(events: RunEvent[]): string | undefined {
  const names = agentNames(events)
  const order: string[] = []
  for (const e of events) {
    if (e.type !== 'agent.started') continue
    const id = str(rec(e.payload).agent_id) ?? e.agent_id
    if (id && names[id] && !order.includes(names[id]) && id !== 'router') order.push(names[id])
  }
  if (order.length === 0) return undefined
  return order.length === 1 ? order[0] : `${order[0]}, com apoio: ${order.slice(1).join(', ')}`
}
