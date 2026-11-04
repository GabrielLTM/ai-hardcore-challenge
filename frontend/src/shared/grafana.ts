/** Grafana local da observabilidade (compilação: VITE_GRAFANA_URL; padrão do Compose). */
export const GRAFANA_URL = ((import.meta.env.VITE_GRAFANA_URL as string | undefined) ?? 'http://localhost:3000').replace(/\/$/, '')

/** Abre o trace no Explore (Tempo): a consulta TraceQL com só o trace_id busca o trace inteiro. */
export function grafanaTraceUrl(traceId: string): string {
  const panes = { a: { datasource: 'tempo', queries: [{ refId: 'A', datasource: { type: 'tempo', uid: 'tempo' }, queryType: 'traceql', query: traceId }], range: { from: 'now-24h', to: 'now' } } }
  return `${GRAFANA_URL}/explore?schemaVersion=1&orgId=1&panes=${encodeURIComponent(JSON.stringify(panes))}`
}
