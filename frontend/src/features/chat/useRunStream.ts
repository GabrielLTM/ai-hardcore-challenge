import { useCallback, useEffect, useLayoutEffect, useReducer, useRef, useState } from 'react'
import { api, isAbort, toProblem, type ApiProblem } from '../../shared/api'
import {
  applyEvents, EVENT_TYPES, initialRunState, isTerminalEvent, isTerminalStatus, readRunInfo,
  type RunEvent, type RunInfo, type RunState,
} from './runs'

const STALL_MS = 15_000

export interface RunStream {
  runId: string | null
  state: RunState
  info: RunInfo | null
  /** Conexão caiu; o texto já recebido permanece e a retomada parte do último seq. */
  reconnecting: boolean
  /** Eventos fora da retenção (410): mostramos o estado final consultado em GET /runs/{id}. */
  expired: boolean
  problem: ApiProblem | null
  retryNow: () => void
}

interface View { runId: string | null; state: RunState; info: RunInfo | null; reconnecting: boolean; expired: boolean; problem: ApiProblem | null }
const blank = (runId: string | null): View => ({ runId, state: initialRunState, info: null, reconnecting: false, expired: false, problem: null })

type Action =
  | { type: 'events'; runId: string; events: RunEvent[] }
  | { type: 'connected'; runId: string }
  | { type: 'reconnecting'; runId: string }
  | { type: 'info'; runId: string; info: RunInfo }
  | { type: 'expired'; runId: string }
  | { type: 'problem'; runId: string; problem: ApiProblem }

function reducer(view: View, a: Action): View {
  const v = view.runId === a.runId ? view : blank(a.runId)
  switch (a.type) {
    case 'events': return { ...v, state: applyEvents(v.state, a.events), reconnecting: false, problem: null }
    case 'connected': return { ...v, reconnecting: false }
    case 'reconnecting': return { ...v, reconnecting: true }
    case 'expired': return { ...v, expired: true, reconnecting: false }
    case 'problem': return { ...v, problem: a.problem, reconnecting: false }
    case 'info': {
      // Sem evento terminal (stream caiu ou expirou) o estado autoritativo vem de GET /runs/{id}.
      let state = v.state
      if (!state.terminal && isTerminalStatus(a.info.status)) {
        state = { ...state, terminal: a.info.status, failure: a.info.status === 'failed' || a.info.status === 'interrupted' ? { message: a.info.errorMessage, requestId: a.info.errorRequestId } : state.failure }
      }
      if (state.terminal && a.info.text.length > state.text.length) state = { ...state, text: a.info.text }
      return { ...v, info: a.info, state, reconnecting: state.terminal ? false : v.reconnecting }
    }
  }
}

/**
 * Assina os eventos de uma execução via EventSource. Reconecta manualmente a partir do último seq
 * (a URL do EventSource nativo ficaria com o cursor antigo) e trata 410 consultando GET /runs/{id}.
 */
export function useRunStream(runId: string | null, enabled: boolean, onTerminal?: (runId: string) => void): RunStream {
  const [view, dispatch] = useReducer(reducer, null, () => blank(runId))
  const [nonce, setNonce] = useState(0)
  const seq = useRef<{ runId: string | null; value: number }>({ runId: null, value: 0 })
  const retry = useRef<(() => void) | null>(null)
  const terminalRef = useRef(onTerminal)
  useLayoutEffect(() => { terminalRef.current = onTerminal })

  useEffect(() => {
    if (!runId || !enabled) return
    if (seq.current.runId !== runId) seq.current = { runId, value: 0 }
    const cursor = seq.current
    const ctl = new AbortController()
    const c = { done: false, finished: false, es: null as EventSource | null, flushTimer: undefined as ReturnType<typeof setTimeout> | undefined }
    const retryTimer: { id?: ReturnType<typeof setTimeout> } = {}
    const retries = { n: 0 }
    const activity = { at: Date.now() }
    const buffer: RunEvent[] = []

    const fetchInfo = async () => {
      const info = readRunInfo(await api(`/runs/${runId}`, { signal: ctl.signal }))
      dispatch({ type: 'info', runId, info })
      return info
    }
    const flush = () => {
      c.flushTimer = undefined
      if (buffer.length) dispatch({ type: 'events', runId, events: buffer.splice(0) })
    }
    const finish = () => {
      if (c.finished) return
      c.finished = true
      c.done = true
      c.es?.close()
      flush()
      fetchInfo().catch(() => {})
      terminalRef.current?.(runId)
    }
    const poll = async () => {
      while (!c.done && !ctl.signal.aborted) {
        try { if (isTerminalStatus((await fetchInfo()).status)) return finish() } catch (e) { if (isAbort(e)) return }
        await new Promise((r) => setTimeout(r, 2000))
      }
    }
    const probeExpired = async () => {
      const probe = new AbortController()
      try {
        const res = await fetch(`/api/v1/runs/${runId}/events?after=${cursor.value}`, { signal: probe.signal, headers: { Accept: 'text/event-stream' } })
        return res.status === 410
      } catch { return false } finally { probe.abort() }
    }
    const recover = async (opened: boolean) => {
      dispatch({ type: 'reconnecting', runId })
      let terminal = false
      try {
        terminal = isTerminalStatus((await fetchInfo()).status)
      } catch (e) {
        if (isAbort(e)) return
        const p = toProblem(e)
        if (p.status === 401 || p.status === 403 || p.status === 404) { dispatch({ type: 'problem', runId, problem: p }); return }
      }
      // 410: o cursor saiu da retenção. Sem eventos para mostrar, vale o estado final de GET /runs/{id}.
      if (!opened && (await probeExpired())) {
        dispatch({ type: 'expired', runId })
        return terminal ? finish() : void poll()
      }
      if (terminal) return finish()
      retries.n++
      retryTimer.id = setTimeout(connect, Math.min(1000 * 2 ** retries.n, 10_000))
    }
    const connect = () => {
      if (c.done || ctl.signal.aborted) return
      const es = (c.es = new EventSource(`/api/v1/runs/${runId}/events?after=${cursor.value}`))
      let opened = false
      es.onopen = () => { opened = true; retries.n = 0; activity.at = Date.now(); dispatch({ type: 'connected', runId }) }
      const onEvent = (m: MessageEvent<string>) => {
        let ev: RunEvent
        try { ev = JSON.parse(m.data) as RunEvent } catch { return }
        if (typeof ev.seq !== 'number' || ev.seq <= cursor.value) return
        cursor.value = ev.seq
        activity.at = Date.now()
        buffer.push(ev)
        if (isTerminalEvent(ev.type)) return finish()
        c.flushTimer ??= setTimeout(flush, 50)
      }
      for (const t of EVENT_TYPES) es.addEventListener(t, onEvent as EventListener)
      es.onerror = () => { es.close(); if (!c.done && !ctl.signal.aborted) void recover(opened) }
    }

    // O EventSource não expõe os heartbeats (comentários SSE) e um proxy pode manter a conexão aberta sem dados:
    // após um silêncio, o estado autoritativo (GET /runs/{id}) diz se a execução terminou, perdeu eventos ou o servidor caiu.
    const watchdog = setInterval(async () => {
      if (c.done || Date.now() - activity.at < STALL_MS) return
      activity.at = Date.now()
      try {
        const info = await fetchInfo()
        if (isTerminalStatus(info.status)) return finish()
        if ((info.seq ?? 0) > cursor.value) { c.es?.close(); connect() }
      } catch (e) {
        if (isAbort(e)) return
        c.es?.close()
        void recover(true)
      }
    }, 5000)

    retry.current = () => { clearTimeout(retryTimer.id); c.es?.close(); retries.n = 0; connect() }
    fetchInfo().catch(() => {})
    connect()
    return () => { c.done = true; clearInterval(watchdog); ctl.abort(); clearTimeout(retryTimer.id); clearTimeout(c.flushTimer); c.es?.close(); retry.current = null }
  }, [runId, enabled, nonce])

  const cur = view.runId === runId ? view : blank(runId)
  return { runId, state: cur.state, info: cur.info, reconnecting: cur.reconnecting, expired: cur.expired, problem: cur.problem, retryNow: useCallback(() => (retry.current ? retry.current() : setNonce((n) => n + 1)), []) }
}
