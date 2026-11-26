import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ToastProvider } from '../shared/toast'
import { StatePanel } from '../shared/ui'
import { Shell } from './Shell'

const ChatPage = lazy(() => import('../features/chat/ChatPage'))
const AgentsPage = lazy(() => import('../features/agents/AgentsPage'))
const AgentEditor = lazy(() => import('../features/agents/AgentEditor'))
const VersionsPage = lazy(() => import('../features/agents/VersionsPage'))
const KnowledgePage = lazy(() => import('../features/knowledge/KnowledgePage'))
const SourcePage = lazy(() => import('../features/knowledge/SourcePage'))
const CurationPage = lazy(() => import('../features/curation/CurationPage'))
const OperatorLogin = lazy(() => import('../features/operator/LoginPage'))
const OperatorLayout = lazy(() => import('../features/operator/Layout'))
const OperatorList = lazy(() => import('../features/operator/ListPage'))
const OperatorDetail = lazy(() => import('../features/operator/DetailPage'))

export default function App() {
  return (
    <ToastProvider>
      <BrowserRouter>
        <Suspense fallback={<div className="page"><StatePanel kind="loading" what="a página" /></div>}>
          <Routes>
            <Route path="atendente" element={<OperatorLogin />} />
            <Route path="atendente/protocolos" element={<OperatorLayout />}>
              <Route index element={<OperatorList />} />
              <Route path=":protocol" element={<OperatorDetail />} />
            </Route>
            <Route element={<Shell />}>
              <Route index element={<Navigate to="/conversas" replace />} />
              <Route path="conversas/:conversationId?" element={<ChatPage />} />
              <Route path="agentes" element={<AgentsPage />} />
              <Route path="agentes/:agentId" element={<AgentEditor />} />
              <Route path="agentes/:agentId/versoes" element={<VersionsPage />} />
              <Route path="base" element={<KnowledgePage />} />
              <Route path="base/:sourceId" element={<SourcePage />} />
              <Route path="curadoria" element={<CurationPage />} />
              <Route path="*" element={<main id="main" tabIndex={-1} className="page"><StatePanel kind="empty" title="Página não encontrada" message="O endereço não existe nesta aplicação." /></main>} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ToastProvider>
  )
}
