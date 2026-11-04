import { DELEGATION_TOOL, type AgentContent } from './types'

export type LineOp = { op: 'equal' | 'add' | 'remove'; text: string }
export interface Change {
  field: string
  label?: string
  before?: unknown
  after?: unknown
  added?: unknown[]
  removed?: unknown[]
  lines?: LineOp[]
}

export const FIELD_LABEL: Record<string, string> = {
  name: 'Nome', description: 'Descrição', kind: 'Tipo de papel', model: 'Modelo', tools: 'Ferramentas',
  delegates: 'Agentes delegáveis', limits: 'Limites', enabled: 'Ativação', instructions: 'Instruções',
}
const ORDER = ['instructions', 'delegates', 'tools', 'limits', 'name', 'description', 'kind', 'model', 'enabled']

/** Diferença por linha (LCS). Textos de prompt são curtos; O(n·m) basta. */
export function lineDiff(before: string, after: string): LineOp[] {
  const a = before.split('\n'), b = after.split('\n')
  const dp: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const out: LineOp[] = []
  let i = 0, j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push({ op: 'equal', text: a[i] }); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ op: 'remove', text: a[i++] })
    else out.push({ op: 'add', text: b[j++] })
  }
  while (i < a.length) out.push({ op: 'remove', text: a[i++] })
  while (j < b.length) out.push({ op: 'add', text: b[j++] })
  return out
}

const same = (x: unknown, y: unknown) => JSON.stringify(x) === JSON.stringify(y)

/** Mesmo formato de GET /agents/{id}/versions/{a}/diff/{b}, para comparar com o rascunho local. */
export function diffContent(before: Partial<AgentContent>, after: Partial<AgentContent>): Change[] {
  const changes: Change[] = []
  for (const field of ORDER) {
    const o = (before as Record<string, unknown>)[field], n = (after as Record<string, unknown>)[field]
    if (field === 'tools' || field === 'delegates') {
      const x = ((o as string[] | undefined) ?? []).filter((t) => t !== DELEGATION_TOOL), y = ((n as string[] | undefined) ?? []).filter((t) => t !== DELEGATION_TOOL)
      if (same([...x].sort(), [...y].sort())) continue
      changes.push({ field, before: x, after: y, added: y.filter((t) => !x.includes(t)), removed: x.filter((t) => !y.includes(t)) })
    } else if (!same(o ?? (field === 'limits' ? {} : ''), n ?? (field === 'limits' ? {} : ''))) {
      changes.push({ field, before: o, after: n, lines: field === 'instructions' ? lineDiff(String(o ?? ''), String(n ?? '')) : undefined })
    }
  }
  return changes
}

export const changedLines = (c: Change) => c.lines?.filter((l) => l.op !== 'equal').length ?? 0
