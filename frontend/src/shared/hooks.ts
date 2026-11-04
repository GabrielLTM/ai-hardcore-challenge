import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { api, isAbort, qs, readPage, toProblem, type ApiProblem } from './api'

export function useOnline(): boolean {
  return useSyncExternalStore(
    (cb) => { window.addEventListener('online', cb); window.addEventListener('offline', cb); return () => { window.removeEventListener('online', cb); window.removeEventListener('offline', cb) } },
    () => navigator.onLine,
    () => true,
  )
}

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (cb) => { const m = window.matchMedia(query); m.addEventListener('change', cb); return () => m.removeEventListener('change', cb) },
    () => window.matchMedia(query).matches,
    () => false,
  )
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

export function useDocumentTitle(title: string) {
  useEffect(() => { document.title = `${title} · Trilha` }, [title])
}

/**
 * Carrega um recurso cancelando a requisição anterior. `key` null desliga a busca.
 * Mantém o dado antigo enquanto recarrega (reload) e o descarta ao trocar de key.
 */
export function useResource<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>) {
  const [tick, setTick] = useState(0)
  const [state, setState] = useState<{ key: string; tick: number; data?: T; problem?: ApiProblem } | null>(null)
  const loadRef = useRef(load)
  useLayoutEffect(() => { loadRef.current = load })
  useEffect(() => {
    if (key === null) return
    const ctl = new AbortController()
    loadRef.current(ctl.signal).then(
      (data) => setState({ key, tick, data }),
      (e) => { if (!isAbort(e)) setState({ key, tick, problem: toProblem(e) }) },
    )
    return () => ctl.abort()
  }, [key, tick])
  const sameKey = state !== null && state.key === key
  const settled = sameKey && state.tick === tick
  return {
    data: sameKey ? state.data : undefined,
    problem: settled ? state.problem : undefined,
    loading: key !== null && !settled,
    reload: useCallback(() => setTick((t) => t + 1), []),
    setData: useCallback((data: T) => setState((s) => (s ? { ...s, data, problem: undefined } : s)), []),
  }
}

type Params = Record<string, string | undefined>

/** Lista com paginação por cursor opaco: "pages" navega anterior/próxima; "append" acumula (Carregar mais). */
export function useCursorList<T>(path: string, params: Params, mode: 'pages' | 'append', limit = 20) {
  const base = `${path}${qs({ ...params, limit })}`
  const [stack, setStack] = useState<{ base: string; cursors: (string | null)[] }>({ base, cursors: [null] })
  const cursors = stack.base === base ? stack.cursors : [null]
  const cursor = cursors[cursors.length - 1]
  const first = useResource(`${base}|${cursor ?? ''}`, async (signal) =>
    readPage<T>(await api(`${base}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal })))

  const [more, setMore] = useState<{ anchor: unknown; items: T[]; next: string | null; busy: boolean; problem?: ApiProblem } | null>(null)
  const moreRef = useRef<AbortController | null>(null)
  const anchor = first.data
  const extra = more && more.anchor === anchor ? more : null

  const loadMore = useCallback(async () => {
    const next = extra ? extra.next : anchor?.next
    if (!next) return
    moreRef.current?.abort()
    const ctl = (moreRef.current = new AbortController())
    setMore(extra ? { ...extra, busy: true, problem: undefined } : { anchor, items: [], next, busy: true })
    try {
      const page = readPage<T>(await api(`${base}&cursor=${encodeURIComponent(next)}`, { signal: ctl.signal }))
      setMore((m) => (m && m.anchor === anchor ? { anchor, items: [...m.items, ...page.items], next: page.next, busy: false } : m))
    } catch (e) {
      if (!isAbort(e)) setMore((m) => (m && m.anchor === anchor ? { ...m, busy: false, problem: toProblem(e) } : m))
    }
  }, [base, extra, anchor])

  const items = mode === 'append' ? [...(first.data?.items ?? []), ...(extra?.items ?? [])] : first.data?.items ?? []
  const hasNext = mode === 'append' ? Boolean(extra ? extra.next : first.data?.next) : Boolean(first.data?.next)
  return {
    items,
    total: first.data?.total,
    loading: first.loading,
    problem: first.problem,
    reload: first.reload,
    page: cursors.length,
    hasNext,
    hasPrev: cursors.length > 1,
    loadingMore: Boolean(extra?.busy),
    moreProblem: extra?.problem,
    loadMore,
    next: () => { const n = first.data?.next; if (n) setStack({ base, cursors: [...cursors, n] }) },
    prev: () => setStack({ base, cursors: cursors.slice(0, -1) }),
  }
}
