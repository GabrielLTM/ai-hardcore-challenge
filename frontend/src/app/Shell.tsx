import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { ConversationList } from '../features/chat/ConversationList'
import { ConversationsContext, useConversations, useConversationsState } from './conversations'
import { GRAFANA_URL } from '../shared/grafana'
import { useOnline } from '../shared/hooks'
import { Icon, Logo, type IconName } from '../shared/icons'
import { Modal } from '../shared/modal'
import { useToast } from '../shared/toast-context'
import { Button, ButtonLink, Chip } from '../shared/ui'

const LINKS: { to: string; label: string; icon: IconName }[] = [
  { to: '/conversas', label: 'Conversas', icon: 'chat' },
  { to: '/agentes', label: 'Agentes', icon: 'agents' },
  { to: '/base', label: 'Base de conhecimento', icon: 'doc' },
]

function SidebarConversations({ onNavigate }: { onNavigate?: () => void }) {
  const { list, items, search, setSearch, live } = useConversations()
  const { pathname } = useLocation()
  const [, section, selectedId] = pathname.split('/')
  const selected = section === 'conversas' ? selectedId : undefined
  return (
    <div className="nav__convs">
      <ConversationList items={items} loading={list.loading} problem={list.problem} search={search} onSearch={setSearch}
        selectedId={selected} liveStatus={live.id === selected ? live.status : undefined}
        hasMore={list.hasNext} loadingMore={list.loadingMore} onLoadMore={() => void list.loadMore()} onRetry={list.reload} onNavigate={onNavigate} />
    </div>
  )
}

export function Nav({ onNavigate, collapsed = false, onToggle }: { onNavigate?: () => void; collapsed?: boolean; onToggle?: () => void }) {
  const toggle = onToggle && (
    <Button className="nav__toggle btn--icon" variant="link" icon="sidebar" aria-expanded={!collapsed}
      aria-label={collapsed ? 'Expandir barra lateral' : 'Recolher barra lateral'} title={collapsed ? 'Expandir barra lateral' : 'Recolher barra lateral'} onClick={onToggle} />
  )
  if (collapsed) {
    return (
      <nav aria-label="Principal" className="nav nav--rail">
        {toggle}
        <Link to="/conversas" aria-label="Trilha, início" className="nav__brand"><Logo /></Link>
        <Link to="/conversas" className="nav__link" aria-label="Nova conversa" title="Nova conversa"><Icon name="plus" /></Link>
        {LINKS.map((l) => (
          <NavLink key={l.to} to={l.to} className="nav__link" aria-label={l.label} title={l.label}><Icon name={l.icon} /></NavLink>
        ))}
        <a href={GRAFANA_URL} target="_blank" rel="noreferrer" className="nav__link" aria-label="Observabilidade (abre em nova aba)" title="Observabilidade"><Icon name="external" /></a>
      </nav>
    )
  }
  return (
    <nav aria-label="Principal" className="nav">
      <div className="nav__top">
        <Link to="/conversas" aria-label="Trilha, início" className="nav__brand" onClick={onNavigate}>
          <Logo />
          <span><strong>Trilha</strong><span>Atendimento com agentes</span></span>
        </Link>
        {toggle}
      </div>
      <div className="nav__workspace"><Chip icon="info">Workspace demo · perfil local-demo</Chip></div>
      <div className="nav__new"><ButtonLink to="/conversas" icon="plus" variant="primary" onClick={onNavigate}>Nova conversa</ButtonLink></div>
      {LINKS.map((l) => (
        <NavLink key={l.to} to={l.to} className="nav__link" onClick={onNavigate}><Icon name={l.icon} />{l.label}</NavLink>
      ))}
      <a href={GRAFANA_URL} target="_blank" rel="noreferrer" className="nav__link"><Icon name="external" />Observabilidade<span className="sr-only"> (abre em nova aba)</span></a>
      <SidebarConversations onNavigate={onNavigate} />
      <div className="nav__user"><div>Operador demo</div><div>Administrador</div></div>
    </nav>
  )
}

const COLLAPSED_KEY = 'trilha.sidebar.collapsed'
function readCollapsed() {
  try { return localStorage.getItem(COLLAPSED_KEY) === '1' } catch { return false }
}

const ShellContext = createContext<{ openMenu: () => void }>({ openMenu: () => {} })

/** Botão que abre o menu em telas estreitas (some a partir de 1100px). */
export function MenuButton({ onClick }: { onClick?: () => void }) {
  const { openMenu } = useContext(ShellContext)
  return <Button className="menu-btn btn--icon" variant="link" icon="menu" aria-label="Abrir navegação e conversas" onClick={onClick ?? openMenu} />
}

export function Shell() {
  const online = useOnline()
  const toast = useToast()
  const { pathname } = useLocation()
  const [menu, setMenu] = useState(false)
  const conversations = useConversationsState()
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const toggleCollapsed = () => setCollapsed((c) => {
    try { localStorage.setItem(COLLAPSED_KEY, c ? '0' : '1') } catch { /* preferência só deste navegador */ }
    return !c
  })
  const section = pathname.split('/')[1]
  const key = section === 'conversas' ? section : pathname
  const previous = useRef(key)
  useEffect(() => {
    // Navegação de SPA: leva o foco ao conteúdo quando a página muda (não a cada troca de conversa).
    if (previous.current === key) return
    previous.current = key
    document.getElementById('main')?.focus({ preventScroll: true })
  }, [key])

  const check = async () => {
    try { const r = await fetch('/api/v1/health/live'); toast(r.ok ? 'Servidor acessível.' : 'O servidor respondeu com erro.', r.ok ? 'default' : 'danger') }
    catch { toast('Ainda sem conexão com o servidor.', 'danger') }
  }

  return (
    <ConversationsContext.Provider value={conversations}>
    <ShellContext.Provider value={{ openMenu: () => setMenu(true) }}>
      <a href="#main" className="skip">Pular para o conteúdo</a>
      <div className={`app${section === 'conversas' ? ' app--fill' : ''}${collapsed ? ' app--collapsed' : ''}`}>
        <Nav collapsed={collapsed} onToggle={toggleCollapsed} />
        <div className="app__body">
          {!online && (
            <div role="status" className="banner">
              <Icon name="wifiOff" size={14} />
              <span>Você está offline. Mensagens não serão enviadas até a conexão voltar.</span>
              <Button small icon="refresh" onClick={() => void check()}>Verificar conexão</Button>
            </div>
          )}
          <Outlet />
        </div>
        {menu && (
          <Modal variant="left" title="Navegação" hideTitle onClose={() => setMenu(false)}>
            <Nav onNavigate={() => setMenu(false)} />
          </Modal>
        )}
      </div>
    </ShellContext.Provider>
    </ConversationsContext.Provider>
  )
}
