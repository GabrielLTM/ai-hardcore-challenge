import { describe, expect, it } from 'vitest'
import { matchesMarker, readEvidence } from './evidence'

const item = (id: number, extra = {}) => ({ evidence_id: id, kind: 'curated', title: `Fonte ${id}`, url: `https://exemplo.test/${id}`, retrieved_on: '2026-11-03', version: 'kb-v3', source_id: `0${id}-x`, chunk_id: `0${id}-x#1`, text: 'trecho', limitations: [], cited: false, ...extra })

describe('readEvidence', () => {
  it('separa trechos citados dos apenas recuperados (formato do backend)', () => {
    const retrieved = [item(1, { cited: true }), item(2), item(3)]
    const e = readEvidence({ retrieved, cited: [retrieved[0]] })
    expect(e.cited.map((c) => c.marker)).toEqual(['1'])
    expect(e.notUsed.map((c) => c.title)).toEqual(['Fonte 2', 'Fonte 3'])
    expect(e.cited[0]).toMatchObject({ kind: 'curated', indexVersion: 'kb-v3', url: 'https://exemplo.test/1' })
  })

  it('tolera resposta vazia ou sem campos', () => {
    expect(readEvidence({})).toEqual({ cited: [], notUsed: [] })
    expect(readEvidence(null)).toEqual({ cited: [], notUsed: [] })
  })

  it('liga o [n] do texto ao marcador ou ao id da fonte', () => {
    const [c] = readEvidence({ retrieved: [item(8, { cited: true })] }).cited
    expect(matchesMarker(c, '8')).toBe(true)
    expect(matchesMarker(c, '08')).toBe(true) // id da fonte 08-x
    expect(matchesMarker(c, '09')).toBe(false)
  })
})
