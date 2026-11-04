import { createContext, useContext, useState } from 'react'
import { useCursorList, useDebounced } from '../shared/hooks'
import type { Conversation } from '../features/chat/types'

/** Conversation history lives in the shell so the sidebar shows it on every page. */
export function useConversationsState() {
  const [search, setSearch] = useState('')
  const q = useDebounced(search.trim(), 250)
  const list = useCursorList<Conversation>('/conversations', { q }, 'append', 30)
  const needle = q.toLowerCase()
  // o servidor pode ignorar `q`; filtrar de novo no cliente é inofensivo
  const items = needle ? list.items.filter((c) => (c.title ?? '').toLowerCase().includes(needle)) : list.items
  // status ao vivo da conversa aberta (a lista do servidor pode estar atrasada)
  const [live, setLive] = useState<{ id?: string; status?: string }>({})
  return { list, items, search, setSearch, live, setLive }
}

export type ConversationsValue = ReturnType<typeof useConversationsState>
export const ConversationsContext = createContext<ConversationsValue | null>(null)

export function useConversations(): ConversationsValue {
  const value = useContext(ConversationsContext)
  if (!value) throw new Error('useConversations fora do Shell')
  return value
}
