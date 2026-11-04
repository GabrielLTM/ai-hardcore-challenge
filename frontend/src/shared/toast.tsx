import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './icons'
import { ToastContext, type Push, type ToastItem } from './toast-context'

/** Toasts não exclusivos: empilham, não roubam o foco e são anunciados de forma educada (role=status). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const seq = useRef(0)
  const dismiss = useCallback((id: number) => setItems((l) => l.filter((t) => t.id !== id)), [])
  const push = useCallback<Push>((text, tone = 'default') => {
    const id = ++seq.current
    setItems((l) => [...l.slice(-3), { id, text, tone }])
    setTimeout(() => dismiss(id), 8000)
  }, [dismiss])
  const value = useMemo(() => push, [push])
  return (
    <ToastContext.Provider value={value}>
      {children}
      {createPortal(
        <div className="toasts" role="status">
          {items.map((t) => (
            <div key={t.id} className={`toast${t.tone === 'danger' ? ' toast--danger' : ''}`}>
              <Icon name={t.tone === 'danger' ? 'alert' : 'check'} />
              <span>{t.text}</span>
              <button type="button" className="btn btn--link btn--icon btn--sm" aria-label="Dispensar" onClick={() => dismiss(t.id)}><Icon name="x" /></button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  )
}
