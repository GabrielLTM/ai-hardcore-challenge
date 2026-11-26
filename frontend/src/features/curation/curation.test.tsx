import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ApiProblem } from '../../shared/api'
import { ProblemPanel } from '../../shared/ui'
import { CurationView, type Curation } from './CurationPage'
import { outcome } from './outcome'

const base: Curation = {
  days: 7, runs: 0, judged: 0, revised: 0, skipped: 0, failed: 0, first_round_approval: null, redone: 0, fixed: 0,
  mean_scores: { fundamentacao: null, cobertura: null, limitacoes: null, seguranca: null },
  criteria: [], agents: [], empty_searches: [], examples: [], suggestions: [],
}
const full: Curation = {
  ...base, runs: 5, judged: 4, revised: 1, skipped: 1, failed: 0, first_round_approval: 0.75, redone: 1, fixed: 1,
  criteria: [{ criterion: 'fundamentacao', label: 'Fundamentação', below_max: 1, rate: 0.25 }],
  agents: [{ agent_id: 'getnet', judged: 4, revised: 1, rate: 0.25 }],
  empty_searches: [{ query: 'taxa pix parcelado', count: 2, last_at: '2026-11-25T12:00:00Z' }],
  examples: [{
    run_id: 'r1', conversation_id: 'c1', question: 'Qual a taxa do Pix?', created_at: '2026-11-25T12:00:00Z', revision: 'done',
    verdicts: ['revisar', 'aprovar'], issues: ['Número sem citação'], agents: ['getnet'], failed: ['fundamentacao'],
    answers: ['Cai em 1 dia.', 'Depende da modalidade [1].'],
    sources: [1, 2, 3, 4, 5].map((n) => ({ evidence_id: `e${n}`, kind: 'knowledge', source_id: `s${n}`, title: n === 1 ? 'Tarifas Getnet' : `Fonte ${n}`, url: `https://example.com/${n === 1 ? 'tarifas' : n}` })),
  }],
  suggestions: [
    { kind: 'search', criterion: 'fundamentacao', target: 'getnet', text: 'Reforce a regra de citar.' },
    { kind: 'knowledge', criterion: null, target: 'taxa pix parcelado', text: 'Inclua uma fonte.' },
  ],
}
const render = (d: Curation) => renderToStaticMarkup(<MemoryRouter><CurationView data={d} names={{ getnet: 'Especialista Getnet' }} /></MemoryRouter>)

describe('curadoria dos vereditos', () => {
  it('sem vereditos nem buscas vazias, explica quando os dados aparecem', () => {
    const out = render(base)
    expect(out).toContain('Nenhum veredito no período')
    expect(out).toContain('conversas em que a resposta usou evidência')
  })

  it('mostra totais, sugestões, critérios, agentes, buscas vazias e exemplos com links', () => {
    const out = render(full)
    expect(out).toContain('Respostas avaliadas</dt><dd>4</dd>')
    expect(out).toContain('Aprovadas de primeira</dt><dd>75%</dd>')
    expect(out).toContain('Revisões que resolveram</dt><dd>1 de 1</dd>')
    expect(out).toContain('<strong>Ajustar a busca</strong>')
    expect(out).toContain('<strong>Completar a base</strong>')
    expect(out).toMatch(/href="\/agentes\/getnet"[^>]*>Especialista Getnet<\/a>/)
    expect(out).toMatch(/aria-pressed="false">Fundamentação<span class="sr-only">[^<]*<\/span><\/button><\/td><td>1<\/td><td>25%/)
    expect(out).toMatch(/Especialista Getnet<\/td><td>4<\/td><td>1<\/td><td>25%/)
    expect(out).toContain('taxa pix parcelado</td><td>2</td>')
    expect(out).toContain('href="/conversas/c1"')
    expect(out).toContain('Nova versão aprovada e enviada')
    expect(out).toContain('aria-label="Abrir conversa: Qual a taxa do Pix?"')
    expect(out).toContain('Primeira versão (o cliente não viu)')
    expect(out).toContain('Depende da modalidade')
    expect(out).toContain('e mais 2')  // only three sources listed
    expect(out).toContain('Número sem citação')
    expect(out).toContain('href="https://example.com/tarifas"')
  })

  it('descreve o que chegou ao cliente em cada desfecho', () => {
    expect(outcome(['revisar', 'revisar'], 'done')).toBe('Nova versão ainda com problemas, enviada assim')
    expect(outcome(['revisar'], 'skipped_budget')).toBe('Sem orçamento para nova versão; enviada a original')
    expect(outcome(['revisar', 'failed'], 'done')).toBe('A nova versão falhou; enviada a original')
  })

  it('403 vira o estado de acesso restrito', () => {
    const out = renderToStaticMarkup(<MemoryRouter><ProblemPanel problem={new ApiProblem(403, 'forbidden', 'Você não tem acesso a este recurso.')} what="a curadoria" /></MemoryRouter>)
    expect(out).toContain('Acesso restrito (403)')
  })
})
