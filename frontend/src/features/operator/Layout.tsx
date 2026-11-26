import { Link, Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Logo } from '../../shared/icons'
import { Button } from '../../shared/ui'
import { LIST_PATH } from './model'
import { clearSession, useOperatorSession } from './session'
import './operator.css'

/** Guarda das rotas do atendente: sem sessão (ou sessão vencida) volta ao login lembrando de onde veio. */
export default function OperatorLayout() {
  const session = useOperatorSession()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  if (!session) return <Navigate to="/atendente" replace state={{ from: pathname }} />
  return (
    <div className="op">
      <a href="#main" className="skip">Pular para o conteúdo</a>
      <header className="op__bar">
        <Link to={LIST_PATH} className="nav__brand" aria-label="Protocolos, início da área do atendente">
          <Logo /><span><strong>Trilha</strong><span>Área do atendente</span></span>
        </Link>
        <div className="op__user">
          <span>{session.operator.name}</span>
          <Button variant="link" onClick={() => { clearSession(); navigate('/atendente', { replace: true }) }}>Sair</Button>
        </div>
      </header>
      <Outlet />
    </div>
  )
}
