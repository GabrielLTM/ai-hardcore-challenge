import { describe, expect, it } from 'vitest'
import { dayGroup, fmtBothZones, fmtDate, fmtDuration } from './format'

describe('format', () => {
  it('data sem horário é dia de calendário e não recua por fuso', () => {
    expect(fmtDate('2026-11-03')).toBe('03/11/2026')
  })
  it('mostra BRT e UTC lado a lado', () => {
    expect(fmtBothZones('2026-11-03T17:31:52Z')).toBe('14:31:52 BRT · 17:31:52 UTC')
  })
  it('agrupa conversas por dia em America/Sao_Paulo', () => {
    const now = new Date('2026-11-03T20:00:00Z')
    expect(dayGroup('2026-11-03T14:00:00Z', now)).toBe('Hoje')
    expect(dayGroup('2026-11-02T20:00:00Z', now)).toBe('Ontem')
    expect(dayGroup('2026-10-20T20:00:00Z', now)).toBe('Anteriores')
  })
  it('duração ausente nunca vira zero', () => {
    expect(fmtDuration(undefined)).toBe('não informado')
    expect(fmtDuration(900)).toBe('900 ms')
    expect(fmtDuration(2100)).toBe('2,1 s')
  })
})
