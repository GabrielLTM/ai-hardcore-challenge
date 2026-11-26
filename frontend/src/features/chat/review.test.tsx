import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { AssistantBubble } from './MessageView'

describe('chat mostra a verificação', () => {
  it('com o texto retido, mostra só "Verificando a resposta" e nenhum texto vazio', () => {
    const out = renderToStaticMarkup(<AssistantBubble phase="reviewing" text="" streaming />)
    expect(out).toContain('Verificando</span>')
    expect(out).toContain('Verificando a resposta…')
    expect(out).toContain('data-phase="reviewing"')
    expect(out).not.toContain('aria-live="off"') // sem bloco de texto
  })

  it('mostra o texto final uma vez quando ele chega', () => {
    const out = renderToStaticMarkup(<AssistantBubble phase="completed" text="Resposta final" />)
    expect(out).not.toContain('Verificando')
    expect(out.split('Resposta final').length - 1).toBe(1)
  })
})
