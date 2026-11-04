import { describe, expect, it } from 'vitest'
import { diffContent, lineDiff } from './diff'

describe('lineDiff', () => {
  it('marca linhas adicionadas e removidas', () => {
    expect(lineDiff('a\nb\nc', 'a\nx\nc')).toEqual([
      { op: 'equal', text: 'a' }, { op: 'remove', text: 'b' }, { op: 'add', text: 'x' }, { op: 'equal', text: 'c' },
    ])
  })
})

describe('diffContent', () => {
  it('ignora delegate_to_agent e ordem em listas', () => {
    const before = { tools: ['b', 'a', 'delegate_to_agent'], delegates: ['x'], instructions: 'um', limits: {} }
    const after = { tools: ['a', 'b'], delegates: ['x', 'y'], instructions: 'um', limits: {} }
    const changes = diffContent(before, after)
    expect(changes.map((c) => c.field)).toEqual(['delegates'])
    expect(changes[0].added).toEqual(['y'])
  })

  it('compara instruções por linha e limites por valor', () => {
    const changes = diffContent({ instructions: 'a\nb', limits: { max_tool_calls: 12 } }, { instructions: 'a\nc', limits: { max_tool_calls: 8 } })
    expect(changes.map((c) => c.field)).toEqual(['instructions', 'limits'])
    expect(changes[0].lines?.filter((l) => l.op !== 'equal')).toHaveLength(2)
  })
})
