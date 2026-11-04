import { useEffect, useId, useRef, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './icons'

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"])'

interface ModalProps {
  title: string
  description?: ReactNode
  onClose: () => void
  /** 'dialog' centralizado; demais são painéis laterais/inferior (drawer/sheet). */
  variant?: 'dialog' | 'left' | 'right' | 'bottom'
  wide?: boolean
  /** Elemento que recebe o foco ao abrir; padrão é o primeiro controle. */
  initialFocus?: RefObject<HTMLElement | null>
  /** Quem recebe o foco ao fechar, quando o gatilho já mudou de estado (ex.: botão que ficou ocupado). */
  returnFocusTo?: RefObject<HTMLElement | null>
  /** Esconde o título visualmente (o rótulo continua para leitores de tela). */
  hideTitle?: boolean
  children: ReactNode
}

/** Diálogo acessível: foco preso, Esc fecha, fundo inerte e foco restaurado ao fechar. Nunca usa diálogos nativos. */
export function Modal({ title, description, onClose, variant = 'dialog', wide, initialFocus, returnFocusTo, hideTitle, children }: ModalProps) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const descId = useId()
  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose })

  useEffect(() => {
    const node = ref.current
    if (!node) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const root = document.getElementById('root')
    root?.setAttribute('inert', '')
    const items = () => [...node.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement)
    ;(initialFocus?.current ?? node.querySelector<HTMLElement>('input:not([type=hidden]):not([disabled]),textarea,select') ?? items()[0] ?? node).focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); closeRef.current(); return }
      if (e.key !== 'Tab') return
      const list = items()
      if (list.length === 0) { e.preventDefault(); node.focus(); return }
      const first = list[0], last = list[list.length - 1]
      if (e.shiftKey && (document.activeElement === first || document.activeElement === node)) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    node.addEventListener('keydown', onKey)
    return () => {
      node.removeEventListener('keydown', onKey)
      root?.removeAttribute('inert')
      // oxlint-disable-next-line react-hooks/exhaustive-deps -- lê o valor mais recente do ref de propósito, ao fechar
      const target = returnFocusTo?.current?.isConnected ? returnFocusTo.current : previous
      if (target?.isConnected && !(target as HTMLButtonElement).disabled) target.focus()
      else document.getElementById('main')?.focus()
    }
  }, [initialFocus, returnFocusTo])

  const body = (
    <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={description ? descId : undefined} tabIndex={-1}
      className={variant === 'dialog' ? `dialog${wide ? ' dialog--wide' : ''}` : `drawer drawer--${variant}`}>
      {variant === 'bottom' && <div className="drawer__grip" aria-hidden="true" />}
      <div className="dialog__head" style={variant === 'dialog' ? undefined : { padding: '12px 12px 0 16px' }}>
        <h2 id={titleId} className={hideTitle ? 'sr-only' : undefined} style={variant === 'dialog' ? undefined : { fontSize: 16, margin: 0 }}>{title}</h2>
        <button type="button" className="btn btn--link btn--icon" aria-label="Fechar" onClick={onClose}><Icon name="x" /></button>
      </div>
      {description && <p id={descId} style={{ margin: variant === 'dialog' ? 0 : '0 16px', color: 'var(--ink-2)' }}>{description}</p>}
      {variant === 'dialog' ? children : <div className="drawer__scroll">{children}</div>}
    </div>
  )
  return createPortal(
    variant === 'dialog'
      ? <div className="scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>{body}</div>
      : <><div className="scrim" role="presentation" style={{ padding: 0 }} onMouseDown={onClose} aria-hidden="true" />{body}</>,
    document.body,
  )
}
