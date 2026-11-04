import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Chip, SearchBox, StatePanel, ProblemPanel } from '../../shared/ui'
import { dayGroup, listTime } from '../../shared/format'
import type { ApiProblem } from '../../shared/api'
import { conversationChip, type Conversation } from './types'
import './chat.css' // the list also renders in the shell sidebar on every page

interface Props {
  items: Conversation[]
  loading: boolean
  problem?: ApiProblem
  search: string
  onSearch: (v: string) => void
  selectedId?: string
  /** Status ao vivo da conversa selecionada (a lista do servidor pode estar atrasada). */
  liveStatus?: string
  hasMore: boolean
  loadingMore: boolean
  onLoadMore: () => void
  onRetry: () => void
  onNavigate?: () => void
}

export function ConversationList({ items, loading, problem, search, onSearch, selectedId, liveStatus, hasMore, loadingMore, onLoadMore, onRetry, onNavigate }: Props) {
  const groups = ['Hoje', 'Ontem', 'Anteriores', 'Sem data']
    .map((g) => ({ g, rows: items.filter((c) => dayGroup(c.updated_at ?? c.created_at) === g) }))
    .filter((x) => x.rows.length > 0)
  const [searching, setSearching] = useState(Boolean(search))
  const searchRef = useRef<HTMLDivElement>(null)
  // focus follows the user's click on the magnifier, never on page load
  useEffect(() => { if (searching) searchRef.current?.querySelector('input')?.focus() }, [searching])
  const toggleSearch = () => { if (searching) onSearch(''); setSearching(!searching) }
  return (
    <section className="convlist" aria-labelledby="titulo-conversas">
      <div className="convlist__head">
        <h2 id="titulo-conversas">Conversas</h2>
        <Button className="btn--icon" variant="link" icon={searching ? 'x' : 'search'} aria-expanded={searching} aria-controls="busca-conversas"
          aria-label={searching ? 'Fechar busca de conversas' : 'Buscar conversas'} title={searching ? 'Fechar busca' : 'Buscar conversas'} onClick={toggleSearch} />
      </div>
      {searching && <div id="busca-conversas" ref={searchRef}><SearchBox value={search} onChange={onSearch} label="Buscar conversas" placeholder="Buscar por título" /></div>}
      {loading && items.length === 0 ? <StatePanel kind="loading" what="conversas" />
        : problem ? <ProblemPanel problem={problem} what="as conversas" onRetry={onRetry} />
        : items.length === 0 ? (search
          ? <StatePanel kind="noresult" query={search} onClear={() => onSearch('')} />
          : <StatePanel kind="empty" title="Nenhuma conversa ainda" message="Comece uma conversa para ver as respostas e o andamento aqui." />)
        : groups.map(({ g, rows }) => (
          <div key={g}>
            <h3 className="convlist__group">{g}</h3>
            <ul className="convlist__items">
              {rows.map((c) => {
                const chip = conversationChip(c, c.id === selectedId ? liveStatus : undefined)
                const current = c.id === selectedId
                return (
                  <li key={c.id}>
                    <Link to={`/conversas/${c.id}`} aria-current={current ? 'page' : undefined} className="convlist__item" onClick={onNavigate}>
                      <span className="convlist__row"><span className="convlist__title">{c.title || 'Conversa sem título'}</span><span className="muted small">{listTime(c.updated_at ?? c.created_at)}</span></span>
                      {chip && <span><Chip tone={chip.tone} icon={chip.icon} className="chip--outline">{chip.label}</Chip></span>}
                    </Link>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      {hasMore && <Button variant="link" busy={loadingMore} onClick={onLoadMore} style={{ alignSelf: 'flex-start' }}>Carregar mais conversas</Button>}
    </section>
  )
}
