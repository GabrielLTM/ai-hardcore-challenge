import { Chip } from '../../shared/ui'
import { FIELD_LABEL, type Change, type LineOp } from './diff'
import { DELEGATION_TOOL, fmtLimits, type Limits } from './types'

export function Tags({ items, empty = 'Nenhuma' }: { items: string[]; empty?: string }) {
  return items.length ? <span className="tags">{items.map((t) => <span key={t} className="tag">{t}</span>)}</span> : <span className="muted">{empty}</span>
}

const SIGN: Record<LineOp['op'], { mark: string; sr: string; cls: string }> = {
  equal: { mark: '', sr: '', cls: '' },
  add: { mark: '+', sr: 'Adicionado: ', cls: 'diff__line--add' },
  remove: { mark: '−', sr: 'Removido: ', cls: 'diff__line--remove' },
}

/** Linhas iguais longe de qualquer mudança ficam recolhidas; só o contexto de 2 linhas aparece. */
function withContext(lines: LineOp[], context = 2): (LineOp | { skipped: number })[] {
  const near = lines.map((_, i) => lines.slice(Math.max(0, i - context), i + context + 1).some((l) => l.op !== 'equal'))
  const out: (LineOp | { skipped: number })[] = []
  lines.forEach((l, i) => {
    if (near[i]) out.push(l)
    else { const last = out[out.length - 1]; if (last && 'skipped' in last) last.skipped++; else out.push({ skipped: 1 }) }
  })
  return out
}

export function DiffLines({ lines }: { lines: LineOp[] }) {
  return (
    <div className="diff mono">
      {withContext(lines).map((l, i) => 'skipped' in l
        ? <div key={i} className="diff__line muted"><span aria-hidden="true" className="diff__mark">…</span><span>{l.skipped} {l.skipped === 1 ? 'linha sem alteração' : 'linhas sem alteração'}</span></div>
        : (
          <div key={i} className={`diff__line ${SIGN[l.op].cls}`}>
            <span aria-hidden="true" className="diff__mark">{SIGN[l.op].mark}</span>
            <span><span className="sr-only">{SIGN[l.op].sr}</span>{l.text || '\u00a0'}</span>
          </div>
        ))}
    </div>
  )
}

const asList = (v: unknown) => (Array.isArray(v) ? (v as unknown[]).filter((x) => x !== DELEGATION_TOOL).map(String) : [])

/** Comparação legível campo a campo: +/− com rótulo em texto, nunca só por cor. */
export function DiffView({ changes, names, identical }: { changes: Change[]; names?: Record<string, string>; identical?: boolean }) {
  if (changes.length === 0) return <p className="muted" style={{ margin: 0 }}>{identical === false ? 'Sem diferenças legíveis.' : 'Sem alterações entre as duas versões.'}</p>
  const nm = (id: string) => names?.[id] ?? id
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {changes.map((c) => (
        <section key={c.field} aria-label={c.label ?? FIELD_LABEL[c.field]} className="diffsec">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <h3 style={{ margin: 0, fontSize: 14 }}>{FIELD_LABEL[c.field] ?? c.label ?? c.field}</h3>
            <Chip tone="warning" icon="pencil">Alterado</Chip>
          </div>
          {c.field === 'instructions' && c.lines
            ? <DiffLines lines={c.lines} />
            : c.field === 'tools' || c.field === 'delegates'
              ? (
                <div className="diff">
                  {asList(c.added ?? (c.after as string[])).filter((x) => !asList(c.before).includes(x) || c.added).map((x) => (
                    <div key={`a${x}`} className="diff__line diff__line--add"><span aria-hidden="true" className="diff__mark">+</span><span><span className="sr-only">Adicionado: </span>{c.field === 'delegates' ? nm(x) : <code>{x}</code>}</span></div>))}
                  {asList(c.removed).map((x) => (
                    <div key={`r${x}`} className="diff__line diff__line--remove"><span aria-hidden="true" className="diff__mark">−</span><span><span className="sr-only">Removido: </span>{c.field === 'delegates' ? nm(x) : <code>{x}</code>}</span></div>))}
                </div>
              )
              : (
                <div className="diff">
                  <div className="diff__line diff__line--remove"><span aria-hidden="true" className="diff__mark">−</span><span><span className="sr-only">Antes: </span>{fmt(c.field, c.before)}</span></div>
                  <div className="diff__line diff__line--add"><span aria-hidden="true" className="diff__mark">+</span><span><span className="sr-only">Depois: </span>{fmt(c.field, c.after)}</span></div>
                </div>
              )}
        </section>
      ))}
    </div>
  )
}

function fmt(field: string, v: unknown): string {
  if (field === 'limits') return fmtLimits(v as Limits)
  if (field === 'enabled') return v ? 'ativo' : 'inativo'
  return String(v ?? '') || '(vazio)'
}
