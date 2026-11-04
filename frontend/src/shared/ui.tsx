import { forwardRef, useId, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { Link, type LinkProps } from 'react-router-dom'
import { ApiProblem } from './api'
import { useOnline } from './hooks'
import { Icon, type IconName } from './icons'
import { useToast } from './toast-context'

/* ---------- Botão e link-botão ---------- */
type Variant = 'default' | 'primary' | 'danger' | 'link' | 'bare'
const cls = (v: Variant, extra?: string) => `btn${v === 'default' ? '' : ` btn--${v}`}${extra ? ` ${extra}` : ''}`

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  icon?: IconName
  busy?: boolean
  small?: boolean
}
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'default', icon, busy, small, className, children, disabled, type = 'button', ...rest }, ref) {
  const off = disabled || busy
  return (
    <button ref={ref} type={type} className={cls(variant, `${small ? 'btn--sm ' : ''}${className ?? ''}`.trim())}
      disabled={off} aria-disabled={off || undefined} aria-busy={busy || undefined} {...rest}>
      {busy ? <Icon name="spinner" spin /> : icon && <Icon name={icon} />}
      {children}
    </button>
  )
})

export function ButtonLink({ variant = 'default', icon, children, ...rest }: LinkProps & { variant?: Variant; icon?: IconName }) {
  return <Link className={cls(variant)} {...rest}>{icon && <Icon name={icon} />}{children}</Link>
}

export function ExternalLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={className} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, maxWidth: '100%' }}>
      {children}<Icon name="external" size={14} /><span className="sr-only"> (abre em nova aba)</span>
    </a>
  )
}

/* ---------- Chip: sempre ícone + texto ---------- */
export type Tone = 'neutral' | 'success' | 'warning' | 'danger' | 'demo' | 'progress'
export function Chip({ tone = 'neutral', icon, spin, children, className }: { tone?: Tone; icon?: IconName; spin?: boolean; children: ReactNode; className?: string }) {
  return (
    <span className={`chip${tone === 'neutral' ? '' : ` chip--${tone}`}${className ? ` ${className}` : ''}`}>
      {icon && <Icon name={icon} size={14} spin={spin} />}{children}
    </span>
  )
}
export const DemoChip = () => <Chip tone="demo" icon="info">Dados de demonstração</Chip>

/* ---------- Campos com rótulo e erro ---------- */
interface FieldBase { label: string; error?: string; hint?: string; labelHidden?: boolean }
function useFieldIds(error?: string, hint?: string) {
  const id = useId()
  const describedBy = [error ? `${id}-err` : '', hint ? `${id}-hint` : ''].filter(Boolean).join(' ') || undefined
  return { id, describedBy }
}
function FieldShell({ id, label, error, hint, labelHidden, children, className }: FieldBase & { id: string; children: ReactNode; className?: string }) {
  return (
    <div className={`field${className ? ` ${className}` : ''}`}>
      <label htmlFor={id} className={`field__label${labelHidden ? ' sr-only' : ''}`}>{label}</label>
      {children}
      {hint && <div id={`${id}-hint`} className="field__hint">{hint}</div>}
      {error && <div id={`${id}-err`} className="field__error"><Icon name="alert" size={14} /><span>{error}</span></div>}
    </div>
  )
}
export const TextField = forwardRef<HTMLInputElement, FieldBase & InputHTMLAttributes<HTMLInputElement>>(function TextField(
  { label, error, hint, labelHidden, className, ...rest }, ref) {
  const { id, describedBy } = useFieldIds(error, hint)
  return (
    <FieldShell id={id} label={label} error={error} hint={hint} labelHidden={labelHidden} className={className}>
      <input ref={ref} id={id} className="input" aria-invalid={error ? true : undefined} aria-describedby={describedBy} {...rest} />
    </FieldShell>
  )
})
export const TextAreaField = forwardRef<HTMLTextAreaElement, FieldBase & TextareaHTMLAttributes<HTMLTextAreaElement>>(function TextAreaField(
  { label, error, hint, labelHidden, className, ...rest }, ref) {
  const { id, describedBy } = useFieldIds(error, hint)
  return (
    <FieldShell id={id} label={label} error={error} hint={hint} labelHidden={labelHidden} className={className}>
      <textarea ref={ref} id={id} className="textarea" aria-invalid={error ? true : undefined} aria-describedby={describedBy} {...rest} />
    </FieldShell>
  )
})
export const SelectField = forwardRef<HTMLSelectElement, FieldBase & SelectHTMLAttributes<HTMLSelectElement>>(function SelectField(
  { label, error, hint, labelHidden, className, children, ...rest }, ref) {
  const { id, describedBy } = useFieldIds(error, hint)
  return (
    <FieldShell id={id} label={label} error={error} hint={hint} labelHidden={labelHidden} className={className}>
      <select ref={ref} id={id} className="select" aria-invalid={error ? true : undefined} aria-describedby={describedBy} {...rest}>{children}</select>
    </FieldShell>
  )
})

/** Busca com ação de limpar. O cancelamento da requisição anterior fica no hook de lista (AbortController). */
export function SearchBox({ value, onChange, label, placeholder }: { value: string; onChange: (v: string) => void; label: string; placeholder?: string }) {
  const id = useId()
  return (
    <div className="search" role="search">
      <div className="search__box">
        <label htmlFor={id} className="sr-only">{label}</label>
        <Icon name="search" />
        <input id={id} type="search" className="input" autoComplete="off" value={value} placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape' && value) { e.stopPropagation(); onChange('') } }} />
      </div>
      {value && <Button small icon="x" onClick={() => onChange('')}>Limpar busca</Button>}
    </div>
  )
}

export function CopyButton({ text, label, small = true }: { text: string; label: string; small?: boolean }) {
  const toast = useToast()
  return (
    <Button variant="link" small={small} icon="copy" aria-label={label} onClick={async () => {
      try { await navigator.clipboard.writeText(text); toast('Copiado para a área de transferência.') }
      catch { toast('Não foi possível copiar. Selecione o texto e copie manualmente.', 'danger') }
    }}>{label}</Button>
  )
}

/* ---------- Estados de página ---------- */
export type StateKind = 'loading' | 'empty' | 'noresult' | 'error' | 'unauthorized' | 'forbidden' | 'timeout' | 'offline' | 'conflict' | 'revoked'

interface StateProps {
  kind: StateKind
  /** Nome do que está sendo carregado, em minúsculas: "conversas", "agentes". */
  what?: string
  title?: string
  message?: ReactNode
  query?: string
  requestId?: string
  onRetry?: () => void
  onClear?: () => void
  actions?: ReactNode
}
const STATE_ICON: Record<StateKind, IconName> = {
  loading: 'spinner', empty: 'chat', noresult: 'search', error: 'alert', unauthorized: 'lock', forbidden: 'lock',
  timeout: 'clock', offline: 'wifiOff', conflict: 'alert', revoked: 'lock',
}

export function StatePanel({ kind, what = 'dados', title, message, query, requestId, onRetry, onClear, actions }: StateProps) {
  if (kind === 'loading') {
    return (
      <div className="skeleton" aria-busy="true">
        <span className="sr-only">Carregando {what}</span>
        <span aria-hidden="true" /><span aria-hidden="true" /><span aria-hidden="true" />
      </div>
    )
  }
  const copy: Record<Exclude<StateKind, 'loading'>, { t: string; m: ReactNode }> = {
    empty: { t: `Nenhum registro em ${what}`, m: 'Quando houver itens, eles aparecem aqui.' },
    noresult: { t: `Nenhum resultado encontrado${query ? ` para “${query}”` : ''}`, m: 'Revise o termo buscado ou limpe a busca para ver tudo.' },
    error: { t: 'Falha ao carregar', m: `Não foi possível carregar ${what}. Se o problema continuar, informe o identificador abaixo.` },
    unauthorized: { t: 'Sessão expirada (401)', m: 'Sua sessão expirou. Entre novamente para continuar; o que você digitou foi mantido neste navegador.' },
    forbidden: { t: 'Acesso restrito (403)', m: 'Você não tem acesso a este recurso. Peça acesso ao responsável pelo workspace.' },
    timeout: { t: 'Resposta demorando', m: 'A solicitação está demorando mais que o esperado. Você pode tentar de novo.' },
    offline: { t: 'Offline', m: 'Você está offline. Mensagens não serão enviadas até a conexão voltar.' },
    conflict: { t: 'Conflito de edição (409/412)', m: 'Esta configuração mudou desde que você abriu. Sua edição foi mantida.' },
    revoked: { t: 'Autorização revogada', m: 'Seu acesso a este recurso foi removido durante o uso. O conteúdo foi ocultado.' },
  }
  const c = copy[kind]
  const tone = kind === 'error' || kind === 'revoked' ? 'danger' : kind === 'timeout' || kind === 'offline' || kind === 'conflict' ? 'warning' : ''
  return (
    <section className={`state${tone ? ` state--${tone}` : ''}`} aria-label={title ?? c.t} role={kind === 'error' || kind === 'revoked' ? 'alert' : undefined}>
      <span className="state__icon"><Icon name={STATE_ICON[kind]} size={18} /></span>
      <h2>{title ?? c.t}</h2>
      <p>{message ?? c.m}</p>
      {requestId && <p>request_id <code>{requestId}</code> <CopyButton text={requestId} label="Copiar request_id" /></p>}
      <div className="state__actions">
        {kind === 'noresult' && onClear && <Button icon="x" onClick={onClear}>Limpar busca</Button>}
        {kind === 'unauthorized' && <Button variant="primary" onClick={() => window.location.reload()}>Entrar novamente</Button>}
        {kind === 'forbidden' || kind === 'revoked' ? <ButtonLink to="/conversas">Voltar para Conversas</ButtonLink> : null}
        {kind === 'offline' && <Button icon="refresh" onClick={() => (onRetry ?? (() => window.location.reload()))()}>Verificar conexão</Button>}
        {onRetry && (kind === 'error' || kind === 'timeout' || kind === 'conflict') && <Button icon="refresh" onClick={onRetry}>Tentar novamente</Button>}
        {actions}
      </div>
    </section>
  )
}

function problemKind(p: ApiProblem, online = true): StateKind {
  if (!online || p.code === 'network') return 'offline'
  if (p.status === 401) return 'unauthorized'
  if (p.status === 403) return 'forbidden'
  if (p.code === 'timeout') return 'timeout'
  if (p.status === 409 || p.status === 412) return 'conflict'
  return 'error'
}

export function ProblemPanel({ problem, what, onRetry }: { problem: ApiProblem; what?: string; onRetry?: () => void }) {
  const online = useOnline()
  const kind = problemKind(problem, online)
  return (
    <StatePanel kind={kind} what={what} onRetry={onRetry} requestId={problem.requestId}
      message={kind === 'error' || kind === 'forbidden' ? problem.message : undefined} />
  )
}

export function Pager({ label, hasPrev, hasNext, onPrev, onNext, summary }: { label: string; hasPrev: boolean; hasNext: boolean; onPrev: () => void; onNext: () => void; summary: ReactNode }) {
  return (
    <div className="table-footer">
      <span aria-live="polite">{summary}</span>
      <nav className="pager" aria-label={label}>
        <Button icon="chevronLeft" disabled={!hasPrev} onClick={onPrev}>Anterior</Button>
        <Button disabled={!hasNext} onClick={onNext}>Próxima<Icon name="chevronRight" /></Button>
      </nav>
    </div>
  )
}

export function Note({ children, tone, icon = 'info' }: { children: ReactNode; tone?: 'warning' | 'danger'; icon?: IconName }) {
  return <div className={`note${tone ? ` note--${tone}` : ''}`} role={tone === 'danger' ? 'alert' : 'note'}><Icon name={icon} size={14} /><span>{children}</span></div>
}
