import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { AssistantBubble, HandoffBanner, OperatorBubble, SystemNotice } from './MessageView'

describe('chat do cliente em atendimento humano', () => {
  it('aviso fixo mostra a espera ou o nome do atendente, sempre com o protocolo', () => {
    const waiting = renderToStaticMarkup(<HandoffBanner handoff={{ protocol: 'ATD-1', status: 'aberto', operator_name: null }} />)
    expect(waiting).toContain('Aguardando um atendente')
    expect(waiting).toContain('ATD-1')
    expect(waiting).toContain('role="status"')
    expect(renderToStaticMarkup(<HandoffBanner handoff={{ protocol: 'ATD-1', status: 'aberto', operator_name: null }} onLeave={() => {}} />)).toContain('Voltar ao assistente')
    const talking = renderToStaticMarkup(<HandoffBanner handoff={{ protocol: 'ATD-1', status: 'em_atendimento', operator_name: 'Ana' }} onLeave={() => {}} />)
    expect(talking).not.toContain('Voltar ao assistente')  // once Ana is talking, only she closes it
    expect(talking).toContain('Você está falando com Ana')
    expect(talking).toContain('ATD-1')
  })

  it('mensagem do atendente tem o selo com o nome e não se parece com a do assistente', () => {
    const op = renderToStaticMarkup(<OperatorBubble text="Oi, sou a Ana." name="Ana" />)
    expect(op).toContain('Atendente Ana')
    expect(op).toContain('bubble--operator')
    expect(op).not.toContain('bubble--assistant')
    const bot = renderToStaticMarkup(<AssistantBubble phase="completed" text="Olá" />)
    expect(bot).not.toContain('Atendente')
  })

  it('aviso do sistema é discreto, sem balão', () => {
    const out = renderToStaticMarkup(<SystemNotice text="Atendimento encerrado. Você voltou a falar com o assistente virtual." />)
    expect(out).toContain('system-notice')
    expect(out).not.toContain('bubble')
  })
})
