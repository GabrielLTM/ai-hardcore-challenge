import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AgentCard } from './AgentsPage'
import { KIND_LABEL, type AgentSummary } from './types'

const reviewer: AgentSummary = {
  id: 'reviewer', name: 'Revisor', description: 'Confere a resposta final.', kind: 'reviewer', enabled: true,
  tools: [], delegates: [], revision: 1, status: 'published', version: 1,
}

describe('tipo Revisor na tela de agentes', () => {
  it('mostra o rótulo Revisor e nenhuma ferramenta ou delegação', () => {
    expect(KIND_LABEL.reviewer).toBe('Revisor')
    const out = renderToStaticMarkup(<MemoryRouter><ul><AgentCard agent={reviewer} names={{}} /></ul></MemoryRouter>)
    expect(out).toMatch(/<span class="chip[^"]*">Revisor<\/span>/)
    expect(out).toMatch(/<dt>Ferramentas<\/dt><dd><span class="muted">Nenhuma<\/span>/)
    expect(out).toMatch(/<dt>Delega para<\/dt><dd><span class="muted">Nenhum<\/span>/)
  })
})
