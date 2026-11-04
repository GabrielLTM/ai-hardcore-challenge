import { describe, expect, it } from 'vitest'
import { applyEvents, buildSteps, buildTech, initialRunState, phaseOf, type RunEvent } from './runs'

let seq = 0
const ev = (type: string, payload: Record<string, unknown> = {}, at = '2026-11-03T17:31:52Z'): RunEvent => ({ run_id: 'r1', seq: ++seq, occurred_at: at, type, payload })
const flow = () => {
  seq = 0
  return [
    ev('run.started', { graph_version: '12', trace_id: '4bf92f3577b34da6a3ce929d0e0e4736' }),
    ev('agent.started', { agent_id: 'router', name: 'Roteador', version: 4 }),
    ev('tool.started', { agent_id: 'router', tool: 'delegate_to_agent' }),
    ev('route.selected', { agent_id: 'router', destination: 'support' }),
    ev('agent.started', { agent_id: 'support', name: 'Atendimento ao Cliente', version: 7 }),
    ev('tool.started', { agent_id: 'support', tool: 'get_terminal_status' }),
    ev('tool.completed', { agent_id: 'support', tool: 'get_terminal_status', summary: '1 registros' }, '2026-11-03T17:31:53Z'),
    ev('tool.started', { agent_id: 'support', tool: 'search_knowledge' }),
    ev('retrieval.completed', { agent_id: 'support', source: 'knowledge', results: [{ evidence_id: 1 }, { evidence_id: 2 }, { evidence_id: 3 }] }),
    ev('tool.completed', { agent_id: 'support', tool: 'search_knowledge', summary: '3 trechos recuperados' }),
    ev('usage.recorded', { agent_id: 'support', usage: { input_tokens: 412, output_tokens: 38 }, cost_usd: null }),
  ]
}

describe('applyEvents', () => {
  it('conta cada seq uma vez só, mesmo com replay e reconexão', () => {
    const delta = (s: string): RunEvent => ({ seq: ++seq, occurred_at: '', type: 'response.delta', payload: { text: s } })
    seq = 0
    const batch = [delta('Olá '), delta('mundo')]
    const once = applyEvents(initialRunState, batch)
    const twice = applyEvents(once, batch)
    expect(twice.text).toBe('Olá mundo')
    expect(twice.events).toHaveLength(0) // deltas não ficam na lista de eventos
    expect(twice.lastSeq).toBe(2)
  })

  it('registra o estado terminal e o motivo da falha', () => {
    seq = 0
    const s = applyEvents(initialRunState, [ev('run.failed', { message: 'Limite atingido' })])
    expect(s.terminal).toBe('failed')
    expect(s.failure?.message).toBe('Limite atingido')
    expect(phaseOf(s)).toBe('failed')
  })
})

describe('phaseOf', () => {
  it('percorre aguardando, roteando, executando e gerando', () => {
    seq = 0
    expect(phaseOf(initialRunState)).toBe('waiting')
    let s = applyEvents(initialRunState, [ev('run.started'), ev('agent.started', { agent_id: 'router', name: 'Roteador' })])
    expect(phaseOf(s)).toBe('routing')
    s = applyEvents(s, [ev('route.selected', { destination: 'support' })])
    expect(phaseOf(s)).toBe('running')
    s = applyEvents(s, [{ seq: 99, occurred_at: '', type: 'response.delta', payload: { text: 'Oi' } }])
    expect(phaseOf(s)).toBe('generating')
  })
})

describe('buildSteps', () => {
  it('traduz eventos reais em passos de atendimento', () => {
    const s = applyEvents(initialRunState, flow())
    const steps = buildSteps(s, phaseOf(s), { citedCount: 1 })
    expect(steps.map((x) => x.title)).toEqual(['Entendeu o pedido', 'Consultou sua maquininha', 'Buscou orientação na base Getnet', 'Preparando a resposta'])
    expect(steps[0].detail).toContain('Atendimento ao Cliente')
    expect(steps[1]).toMatchObject({ status: 'done', demo: true, detail: '1 maquininha consultada.' })
    expect(steps[2].detail).toBe('3 trechos encontrados, 1 usado na resposta.')
  })

  it('mostra o encaminhamento ao atendimento humano com o protocolo', () => {
    seq = 0
    const s = applyEvents(initialRunState, [
      ev('agent.started', { agent_id: 'escalation', name: 'Atendimento Humano' }),
      ev('tool.started', { agent_id: 'escalation', tool: 'request_human_handoff', arguments: { reason: 'cliente_pediu_humano' } }),
      ev('tool.completed', { agent_id: 'escalation', tool: 'request_human_handoff', summary: 'Protocolo ATD-20261104-K7M2' }),
    ])
    const step = buildSteps(s, 'running').find((x) => x.id.startsWith('tool-'))
    expect(step).toMatchObject({ status: 'done', title: 'Encaminhou para atendimento humano', detail: 'Protocolo ATD-20261104-K7M2', demo: true })
  })
  it('mostra falha de ferramenta e cancelamento sem esconder o texto parcial', () => {
    seq = 0
    const failed = applyEvents(initialRunState, [ev('run.started'), ev('tool.started', { agent_id: 'a', tool: 'web_search' }), ev('tool.failed', { agent_id: 'a', tool: 'web_search', message: 'Fonte indisponível no momento.' }), ev('run.cancelled')])
    const steps = buildSteps(failed, phaseOf(failed))
    expect(steps.find((x) => x.status === 'error')?.title).toBe('A busca na web não está disponível agora')
    expect(steps.at(-1)).toMatchObject({ status: 'cancelled', title: 'Parado por você' })
  })
})

describe('buildTech', () => {
  it('nunca troca custo desconhecido por zero', () => {
    const s = applyEvents(initialRunState, flow())
    const t = buildTech('r1', s)
    expect(t.cost.known).toBe(false)
    expect(t.tokens).toEqual({ input: 412, output: 38 })
    expect(t.traceId).toBe('4bf92f3577b34da6a3ce929d0e0e4736')
    expect(t.agents.map((a) => a.name)).toEqual(['Roteador', 'Atendimento ao Cliente'])
  })

  it('soma custos informados e marca parcial quando falta preço', () => {
    seq = 0
    const s = applyEvents(initialRunState, [ev('usage.recorded', { usage: { input_tokens: 1 }, cost_usd: 0.002 }), ev('usage.recorded', { usage: { input_tokens: 1 }, cost_usd: null })])
    expect(buildTech('r', s).cost).toEqual({ usd: 0.002, partial: true, known: true })
  })
})
