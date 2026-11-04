import type { ReactNode } from 'react'

/**
 * Markdown mínimo renderizado direto como elementos React (nunca HTML bruto), logo sanitizado por construção.
 * Suporta parágrafos, listas, citações, código, **negrito**, *itálico*, `código`, links http(s)/mailto e [n] de fonte.
 * shortcut: sem tabelas nem listas aninhadas; se o backend passar a emitir tabelas, trocar por um parser completo.
 */
const INLINE = /(\*\*[^*\n]+\*\*)|(`[^`\n]+`)|(\[([^\]\n]+)\]\(((?:https?:\/\/|mailto:)[^\s)]+)\))|(\*[^*\s][^*\n]*\*)|(\[(\d{1,3})\])|(https?:\/\/[^\s<]*[^\s<.,;:!?)])/g

interface InlineProps { onCite?: (marker: string) => void }

function inline(text: string, props: InlineProps, key = 'i', depth = 0): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  let n = 0
  for (const m of text.matchAll(INLINE)) {
    const idx = m.index ?? 0
    if (idx > last) out.push(text.slice(last, idx))
    const k = `${key}-${n++}`
    if (m[1]) out.push(<strong key={k}>{depth < 2 ? inline(m[1].slice(2, -2), props, k, depth + 1) : m[1].slice(2, -2)}</strong>)
    else if (m[2]) out.push(<code key={k}>{m[2].slice(1, -1)}</code>)
    else if (m[3]) out.push(<ExternalAnchor key={k} href={m[5]}>{m[4]}</ExternalAnchor>)
    else if (m[6]) out.push(<em key={k}>{m[6].slice(1, -1)}</em>)
    else if (m[7]) {
      const marker = m[8]
      out.push(props.onCite
        ? <button key={k} type="button" className="cite" aria-label={`Fonte [${marker}]: abrir na aba Fontes`} onClick={() => props.onCite?.(marker)}>[{marker}]</button>
        : <span key={k}>[{marker}]</span>)
    } else if (m[9]) out.push(<ExternalAnchor key={k} href={m[9]}>{m[9]}</ExternalAnchor>)
    last = idx + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

function ExternalAnchor({ href, children }: { href: string; children: ReactNode }) {
  const external = href.startsWith('http')
  return (
    <a href={href} {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : { rel: 'noopener noreferrer' })}>
      {children}{external && <span className="sr-only"> (abre em nova aba)</span>}
    </a>
  )
}

type Block =
  | { t: 'p'; lines: string[] }
  | { t: 'ul' | 'ol'; items: string[] }
  | { t: 'quote'; lines: string[] }
  | { t: 'code'; text: string }
  | { t: 'h'; level: number; text: string }

function parse(src: string): Block[] {
  const blocks: Block[] = []
  const lines = src.replace(/\r\n/g, '\n').split('\n')
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (/^\s*$/.test(line)) continue
    if (line.startsWith('```')) {
      const body: string[] = []
      for (i++; i < lines.length && !lines[i].startsWith('```'); i++) body.push(lines[i])
      blocks.push({ t: 'code', text: body.join('\n') })
      continue
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) { blocks.push({ t: 'h', level: h[1].length, text: h[2] }); continue }
    const li = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line)
    if (li) {
      const ordered = /\d/.test(li[1])
      const items = [li[2]]
      while (i + 1 < lines.length) {
        const next = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i + 1])
        if (next && /\d/.test(next[1]) === ordered) { items.push(next[2]); i++ }
        else if (/^\s{2,}\S/.test(lines[i + 1]) && !next) { items[items.length - 1] += ` ${lines[i + 1].trim()}`; i++ }
        else break
      }
      blocks.push({ t: ordered ? 'ol' : 'ul', items })
      continue
    }
    if (line.startsWith('>')) {
      const body = [line.replace(/^>\s?/, '')]
      while (i + 1 < lines.length && lines[i + 1].startsWith('>')) body.push(lines[++i].replace(/^>\s?/, ''))
      blocks.push({ t: 'quote', lines: body })
      continue
    }
    const para = [line]
    while (i + 1 < lines.length && !/^\s*$/.test(lines[i + 1]) && !/^(```|#{1,4}\s|>|\s*([-*+]|\d+[.)])\s)/.test(lines[i + 1])) para.push(lines[++i])
    blocks.push({ t: 'p', lines: para })
  }
  return blocks
}

const lineBreaks = (lines: string[], props: InlineProps, key: string, tail?: ReactNode) =>
  lines.flatMap((l, i) => [i > 0 ? <br key={`${key}-br${i}`} /> : null, ...inline(l, props, `${key}-${i}`)]).concat(tail ?? [])

export function Markdown({ text, onCite, caret }: { text: string; onCite?: (marker: string) => void; caret?: boolean }) {
  const props = { onCite }
  const blocks = parse(text)
  const tail = caret ? <span key="caret" className="caret" aria-hidden="true" /> : undefined
  return (
    <div className="md">
      {blocks.map((b, i) => {
        const last = i === blocks.length - 1
        const t = last ? tail : undefined
        const key = `b${i}`
        switch (b.t) {
          case 'p': return <p key={key}>{lineBreaks(b.lines, props, key, t)}</p>
          case 'h': return <h4 key={key}>{inline(b.text, props, key)}{t}</h4>
          case 'quote': return <blockquote key={key}>{lineBreaks(b.lines, props, key, t)}</blockquote>
          case 'code': return <pre key={key}><code>{b.text}</code>{t}</pre>
          default: {
            const List = b.t
            return <List key={key}>{b.items.map((it, j) => <li key={j}>{inline(it, props, `${key}-${j}`)}{last && j === b.items.length - 1 ? t : null}</li>)}</List>
          }
        }
      })}
      {blocks.length === 0 && tail}
    </div>
  )
}
