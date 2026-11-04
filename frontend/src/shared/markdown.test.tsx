import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Markdown } from './markdown'

const html = (text: string, onCite?: (m: string) => void) => renderToStaticMarkup(<Markdown text={text} onCite={onCite} />)

describe('Markdown sanitizado', () => {
  it('nunca emite HTML bruto', () => {
    const out = html('<script>alert(1)</script> <img src=x onerror=alert(1)>')
    expect(out).not.toContain('<script')
    expect(out).not.toContain('<img')
    expect(out).toContain('&lt;script&gt;')
  })

  it('só vira link com http(s) ou mailto e abre em nova aba com rel seguro', () => {
    expect(html('[x](javascript:alert(1))')).not.toContain('<a')
    const out = html('[Getnet](https://site.getnet.com.br/) e https://exemplo.test/a.')
    expect(out.match(/rel="noopener noreferrer"/g)).toHaveLength(2)
    expect(out).toContain('target="_blank"')
  })

  it('renderiza listas, negrito e código', () => {
    const out = html('Passos:\n\n1. Desligue\n2. **Ligue** o `chip`')
    expect(out).toContain('<ol>')
    expect(out).toContain('<strong>Ligue</strong>')
    expect(out).toContain('<code>chip</code>')
  })

  it('transforma [n] em botão só quando há destino de citação', () => {
    expect(html('Veja [1].', () => {})).toContain('class="cite"')
    expect(html('Veja [1].')).not.toContain('<button')
  })

  it('mostra o cursor de geração dentro do último bloco', () => {
    expect(renderToStaticMarkup(<Markdown text="Consultei o" caret />)).toContain('class="caret"')
  })
})
