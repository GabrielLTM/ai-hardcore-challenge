import { useRef, useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { api, toProblem, type ApiProblem } from '../../shared/api'
import { useDocumentTitle, useResource } from '../../shared/hooks'
import { Logo } from '../../shared/icons'
import { Button, Note, TextField } from '../../shared/ui'
import { loginTarget } from './model'
import { saveSession, sessionExpired, useOperatorSession, type OperatorSession } from './session'
import './operator.css'

export interface DemoAccount { name: string; email: string; password: string }

/** Botões que só preenchem o formulário; sem contas (fora do perfil de demonstração) não renderiza nada. */
export function DemoAccounts({ accounts, onPick }: { accounts: DemoAccount[] | undefined; onPick: (a: DemoAccount) => void }) {
  if (!accounts?.length) return null
  return (
    <div className="op-login__demo">
      <p className="small muted">Contas de teste: o botão preenche e-mail e senha.</p>
      {accounts.map((a) => <Button key={a.email} onClick={() => onPick(a)}>Usar {a.name}</Button>)}
    </div>
  )
}


export default function LoginPage() {
  useDocumentTitle('Entrar · Área do atendente')
  const session = useOperatorSession()
  const navigate = useNavigate()
  const target = loginTarget((useLocation().state as { from?: string } | null)?.from)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<ApiProblem | null>(null)
  const [tried, setTried] = useState(false)
  const [expired] = useState(sessionExpired)
  const emailRef = useRef<HTMLInputElement>(null)
  // 404 fora do perfil de demonstração: sem botões
  const demo = useResource('op-demo-accounts', async (signal) => (await api<{ items: DemoAccount[] }>('/operator/demo-accounts', { signal })).items)

  if (session) return <Navigate to={target} replace />

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !password) { setTried(true); setProblem(null); emailRef.current?.focus(); return }
    setBusy(true); setProblem(null)
    try {
      const s = await api<OperatorSession>('/operator/login', { method: 'POST', body: { email: email.trim(), password } })
      saveSession(s)
      navigate(target, { replace: true })
    } catch (err) {
      setProblem(toProblem(err))
      setBusy(false)
    }
  }
  const missing = (v: string) => (tried && !v.trim() ? 'Campo obrigatório.' : undefined)

  return (
    <main id="main" tabIndex={-1} className="op-login" aria-labelledby="op-login-t">
      <div className="op-login__card card card--outlined">
        <div className="nav__brand"><Logo /><span><strong>Trilha</strong><span>Área do atendente</span></span></div>
        <h1 id="op-login-t">Entrar</h1>
        {expired && <Note tone="warning" icon="lock">Sua sessão expirou. Entre novamente para continuar.</Note>}
        <Note>Credenciais de demonstração: use as contas abaixo do formulário. Não use senhas reais.</Note>
        <form className="op-login__form" onSubmit={(e) => void submit(e)} noValidate>
          <TextField ref={emailRef} label="E-mail" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} error={missing(email)} required />
          <TextField label="Senha" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} error={missing(password)} required />
          {problem && (
            <div role="alert" className="note note--danger">
              <span>{problem.status === 401 ? 'E-mail ou senha inválidos.' : problem.message}{problem.requestId && <> request_id <code>{problem.requestId}</code></>}</span>
            </div>
          )}
          <Button type="submit" variant="primary" busy={busy}>Entrar</Button>
        </form>
        <DemoAccounts accounts={demo.data} onPick={(a) => { setEmail(a.email); setPassword(a.password); setProblem(null) }} />
      </div>
    </main>
  )
}
