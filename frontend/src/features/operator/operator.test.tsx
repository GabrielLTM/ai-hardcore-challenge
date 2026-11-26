import { isValidElement, type ReactElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CloseForm, DetailView } from './DetailPage'
import OperatorLayout from './Layout'
import { DemoAccounts, type DemoAccount } from './LoginPage'
import { QueueView } from './ListPage'
import { claimHandoff, detailMode, elapsed, humanize, LIST_PATH, loginTarget, validateResolution, type Detail, type Ticket } from './model'
import { clearSession, getSession, opApi, saveSession } from './session'

const ACCOUNTS: DemoAccount[] = [
  { name: 'Ana', email: 'ana@atendimento.demo', password: 'demo-123' },
  { name: 'Bruno', email: 'bruno@atendimento.demo', password: 'demo-123' },
]
const ANA = { id: 'op-ana', name: 'Ana', email: 'ana@atendimento.demo' }
const login = () => saveSession({ token: 'tok-ana', expires_at: new Date(Date.now() + 3_600_000).toISOString(), operator: ANA })

/** Junta o texto de uma árvore de elementos sem renderizar (componentes sem hooks). */
const textOf = (n: ReactNode): string =>
  typeof n === 'string' || typeof n === 'number' ? String(n) : Array.isArray(n) ? n.map(textOf).join('') : isValidElement(n) ? textOf((n.props as { children?: ReactNode }).children) : ''
const buttons = (n: ReactNode): ReactElement<{ onClick: () => void; children: ReactNode }>[] => {
  if (Array.isArray(n)) return n.flatMap(buttons)
  if (!isValidElement(n)) return []
  const props = n.props as { onClick?: () => void; children?: ReactNode }
  return [...(props.onClick ? [n as ReactElement<{ onClick: () => void; children: ReactNode }>] : []), ...buttons(props.children)]
}

const ticket = (over: Partial<Ticket> = {}): Ticket => ({
  protocol: 'ATD-20261123-0001', status: 'aberto', reason: 'cliente_pediu_humano', summary: 'Cliente quer falar com uma pessoa sobre a maquininha.',
  conversation_id: 'c1', user_id: 'cliente1988', created_at: '2026-11-23T12:00:00Z', ...over,
})
const detail = (t: Ticket): Detail => ({
  handoff: { ...t, timeline: [{ kind: 'opened', at: t.created_at, reason: t.reason }] },
  messages: [
    { id: 'm1', role: 'user', content: 'Quero falar com um atendente', created_at: t.created_at },
    { id: 'm2', role: 'assistant', content: 'Protocolo **ATD-1** registrado.' },
    { id: 'm3', role: 'system', content: 'Atendente Bruno entrou na conversa.' },
    { id: 'm4', role: 'operator', content: 'Oi, sou o Bruno.', author_name: 'Bruno' },
  ],
  customer: { user_id: 'cliente1988', receivables: [{ sale_date: '2026-11-20', due_date: '2026-11-21', amount: '150.00', status: 'pago', payment_type: 'debito' }], terminals: [{ model: 'POS X', connectivity: '4G', error: null, serial_suffix: '1234' }] },
})
const noop = async () => true

afterEach(() => { clearSession(); vi.unstubAllGlobals() })

describe('login do atendente', () => {
  it('botões de demonstração preenchem com a conta escolhida', () => {
    const onPick = vi.fn()
    const tree = DemoAccounts({ accounts: ACCOUNTS, onPick })
    const bts = buttons(tree)
    expect(bts.map((b) => textOf(b.props.children))).toEqual(['Usar Ana', 'Usar Bruno'])
    bts[1].props.onClick()
    expect(onPick).toHaveBeenCalledWith(ACCOUNTS[1])
  })

  it('sem contas de demonstração (404) não mostra os botões', () => {
    expect(DemoAccounts({ accounts: undefined, onPick: vi.fn() })).toBeNull()
    expect(DemoAccounts({ accounts: [], onPick: vi.fn() })).toBeNull()
  })

  it('sem sessão a lista não é exibida e o login devolve para a lista', () => {
    const at = (path: string) => renderToStaticMarkup(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/atendente" element={<p>tela de login</p>} />
          <Route path={LIST_PATH} element={<OperatorLayout />}><Route index element={<p>lista protegida</p>} /></Route>
        </Routes>
      </MemoryRouter>,
    )
    expect(at(LIST_PATH)).not.toContain('lista protegida')
    login()
    expect(at(LIST_PATH)).toContain('lista protegida')
    expect(loginTarget(undefined)).toBe(LIST_PATH)
    expect(loginTarget(`${LIST_PATH}/ATD-1`)).toBe(`${LIST_PATH}/ATD-1`)
    expect(loginTarget('https://fora.test/')).toBe(LIST_PATH)
  })

  it('401 numa rota de atendente encerra a sessão; o token só vai nas rotas /operator', async () => {
    login()
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ code: 'unauthorized', message: 'Sessão expirada.' }), { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(opApi('/handoffs')).rejects.toMatchObject({ status: 401 })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/v1/operator/handoffs')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-ana')
    expect(getSession()).toBeNull()
  })
})

describe('lista de protocolos', () => {
  const html = (props: Parameters<typeof QueueView>[0]) => renderToStaticMarkup(<MemoryRouter><QueueView {...props} /></MemoryRouter>)

  it('mostra as três abas com a selecionada marcada e o estado vazio da aba', () => {
    const out = html({ tab: 'meus', onTab: vi.fn(), items: [], onRetry: vi.fn() })
    expect(out.match(/role="tab"/g)).toHaveLength(3)
    expect(out).toMatch(/aria-selected="true"[^>]*>Meus</)
    expect(out).toContain('Nenhum protocolo em Meus')
  })

  it('cada linha traz protocolo, motivo traduzido, espera e quem assumiu', () => {
    const now = Date.parse('2026-11-23T12:07:30Z')
    const out = html({ tab: 'abertos', onTab: vi.fn(), onRetry: vi.fn(), now, items: [ticket(), ticket({ protocol: 'ATD-2', reason: 'assunto_sensivel', status: 'em_atendimento', operator: { id: 'b', name: 'Bruno' } })] })
    expect(out).toContain('ATD-20261123-0001')
    expect(out).toContain('Cliente pediu atendente')
    expect(out).toContain('Assunto sensível')
    expect(out).toContain('7 min')
    expect(out).toContain('Bruno')
    expect(out).toContain('Ninguém ainda')
  })

  it('encerrados mostram a duração até o encerramento', () => {
    expect(elapsed({ created_at: '2026-11-23T12:00:00Z', closed_at: '2026-11-23T13:05:00Z' })).toBe('1 h 05 min')
    expect(elapsed({ created_at: '2026-11-03T12:00:00Z', closed_at: '2026-11-25T15:00:00Z' })).toBe('22 dias 3 h')
    expect(elapsed({ created_at: '2026-11-24T12:00:00Z', closed_at: '2026-11-25T12:30:00Z' })).toBe('1 dia')
    expect(humanize('credito_a_vista')).toBe('Crédito à vista')
    expect(humanize('previsto')).toBe('Previsto')
    expect(html({ tab: 'encerrados', onTab: vi.fn(), items: [], onRetry: vi.fn() })).toContain('Nenhum protocolo em Encerrados')
  })
})

describe('detalhe do protocolo', () => {
  it('409 ao assumir devolve o nome de quem assumiu e a tela fica somente leitura', async () => {
    login()
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code: 'handoff_already_claimed', message: 'Protocolo já assumido por Bruno.', request_id: 'r1', details: { operator_name: 'Bruno' } }), { status: 409 })))
    const r = await claimHandoff('ATD-20261123-0001')
    expect(r).toEqual({ conflict: 'Bruno' })
    const t = ticket({ status: 'em_atendimento', operator: { id: 'op-bruno', name: 'Bruno' } })
    expect(detailMode(t, ANA.id, 'Bruno')).toBe('readonly')
    const out = renderToStaticMarkup(<MemoryRouter><DetailView detail={detail(t)} meId={ANA.id} conflict="Bruno" onClaim={vi.fn()} onSend={noop} onNote={noop} onClose={vi.fn()} /></MemoryRouter>)
    expect(out).toContain('Bruno já assumiu este protocolo')
    expect(out).not.toContain('Assumir conversa')
    expect(out).not.toContain('Enviar mensagem')
    expect(out).not.toContain('Encerrar atendimento')
  })

  it('identifica cada autor e mostra os dados do cliente', () => {
    const t = ticket({ status: 'em_atendimento', operator: { id: ANA.id, name: 'Ana' } })
    const out = renderToStaticMarkup(<MemoryRouter><DetailView detail={detail(t)} meId={ANA.id} onClaim={vi.fn()} onSend={noop} onNote={noop} onClose={vi.fn()} /></MemoryRouter>)
    for (const s of ['Cliente', 'Assistente', 'Atendente Bruno', 'Atendente Bruno entrou na conversa.', 'POS X', 'Enviar mensagem', 'Encerrar atendimento', 'Adicionar nota', 'Trilha de auditoria']) expect(out).toContain(s)
    expect(out).not.toContain('Assumir conversa')
    expect(out).toContain('<strong>ATD-1</strong>')  // a resposta do assistente vem em markdown
  })

  it('protocolo aberto oferece Assumir conversa e nada de enviar ou encerrar', () => {
    const out = renderToStaticMarkup(<MemoryRouter><DetailView detail={detail(ticket())} meId={ANA.id} onClaim={vi.fn()} onSend={noop} onNote={noop} onClose={vi.fn()} /></MemoryRouter>)
    expect(out).toContain('Assumir conversa')
    expect(out).not.toContain('Enviar mensagem')
  })

  it('encerrar sem nota de resolução fica bloqueado', () => {
    const out = renderToStaticMarkup(<CloseForm onSubmit={async () => undefined} onCancel={vi.fn()} />)
    expect(out).toMatch(/<button[^>]*disabled=""[^>]*>Encerrar atendimento<\/button>/)
    expect(validateResolution('   ')).toBe('Escreva a nota de resolução para encerrar.')
    expect(validateResolution('Resolvido trocando o chip')).toBeUndefined()
  })
})
