/** GET /runs/{id}/evidence: trechos recuperados vs. fontes efetivamente citadas. Tolera variações de nomes. */
export type SourceKind = 'curated' | 'official' | 'web' | 'unknown'

export interface EvidenceItem {
  key: string
  marker?: string
  sourceId: string
  chunkId?: string
  title: string
  text: string
  url?: string
  retrievedOn?: string
  kind: SourceKind
  limitations: string[]
  indexVersion?: string
  language?: string
  region?: string
  note?: string
}
export interface Evidence { cited: EvidenceItem[]; notUsed: EvidenceItem[] }

const rec = (v: unknown) => (v && typeof v === 'object' ? (v as Record<string, unknown>) : {})
const str = (v: unknown) => (typeof v === 'string' && v ? v : undefined)

export function kindOf(v: unknown): SourceKind {
  const s = String(v ?? '').toLowerCase()
  if (s.includes('web')) return 'web'
  if (s.includes('official') || s.includes('oficial')) return 'official'
  if (s.includes('curat') || s.includes('curad')) return 'curated'
  return 'unknown'
}

function item(raw: unknown): EvidenceItem {
  const r = rec(raw)
  const sourceId = str(r.source_id) ?? str(r.id) ?? ''
  const chunkId = str(r.chunk_id)
  // evidence_id é o número que o modelo cita como [n]
  const evidenceId = typeof r.evidence_id === 'number' || typeof r.evidence_id === 'string' ? String(r.evidence_id) : undefined
  const lim = r.limitations ?? r.limitation
  return {
    key: evidenceId ? `e${evidenceId}` : chunkId ?? `${sourceId}|${str(r.url) ?? str(r.source_url) ?? ''}`,
    marker: evidenceId ?? str(r.marker) ?? (typeof r.citation === 'number' ? String(r.citation) : str(r.citation)),
    sourceId,
    chunkId,
    title: str(r.title) ?? 'Fonte sem título',
    text: str(r.text) ?? str(r.excerpt) ?? str(r.snippet) ?? '',
    url: str(r.source_url) ?? str(r.url),
    retrievedOn: str(r.retrieved_on) ?? str(r.retrieved_at),
    kind: kindOf(r.kind ?? r.document_type ?? r.source_type ?? r.type),
    limitations: Array.isArray(lim) ? lim.filter((x): x is string => typeof x === 'string') : typeof lim === 'string' ? [lim] : [],
    indexVersion: str(r.index_version) ?? str(r.version),
    language: str(r.language),
    region: str(r.region),
    note: str(r.note) ?? (r.available === false || r.unavailable === true ? 'Fonte original indisponível agora; exibindo a síntese coletada.' : undefined),
  }
}

export function readEvidence(raw: unknown): Evidence {
  const r = rec(raw)
  const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])
  const retrievedRaw = arr(r.retrieved ?? r.retrieved_chunks ?? r.items)
  const citedRaw = arr(r.cited ?? r.citations)
  const citedIds = new Set<string>()
  const cited: EvidenceItem[] = []
  for (const c of citedRaw) {
    if (typeof c === 'string') { citedIds.add(c); continue }
    const it = item(c)
    cited.push(it)
    for (const id of [it.key, it.chunkId, it.sourceId]) if (id) citedIds.add(id)
  }
  const retrieved = retrievedRaw.map(item)
  retrieved.forEach((it, i) => {
    const flagged = rec(retrievedRaw[i]).cited === true || citedIds.has(it.key) || (!!it.chunkId && citedIds.has(it.chunkId)) || (!it.chunkId && !!it.sourceId && citedIds.has(it.sourceId))
    if (flagged && !cited.some((c) => c.key === it.key)) cited.push(it)
  })
  cited.sort((a, b) => Number(a.marker ?? 1e6) - Number(b.marker ?? 1e6))
  cited.forEach((c, i) => { c.marker ??= String(i + 1) })
  return { cited, notUsed: retrieved.filter((it) => !cited.some((c) => c.key === it.key)) }
}

/** Um [n] no texto pode referir o marcador, a posição ou o id da fonte (ex.: [08] ↔ 08-faq-...). */
export const matchesMarker = (it: EvidenceItem, marker: string) =>
  it.marker === marker || it.sourceId === marker || it.sourceId.startsWith(`${marker}-`)
