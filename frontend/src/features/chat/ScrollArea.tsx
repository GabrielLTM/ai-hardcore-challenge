import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Button } from '../../shared/ui'

/** Só acompanha o fim enquanto o usuário está no fim; ao se afastar oferece "Ir para o fim". */
export function ScrollArea({ version, nonce, children }: { version: string; nonce: number; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const [atBottom, setAtBottom] = useState(true)
  const toEnd = (smooth = false) => {
    const el = ref.current
    if (!el) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.scrollTo({ top: el.scrollHeight, behavior: smooth && !reduce ? 'smooth' : 'auto' })
  }
  // The button depends on geometry, not only on scroll events: content can grow, shrink or be swapped without one.
  const measure = () => {
    const el = ref.current
    if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 48)
  }
  useLayoutEffect(() => { if (stick.current) toEnd(); measure() }, [version])
  useLayoutEffect(() => { stick.current = true; toEnd(); measure() }, [nonce])
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    for (const child of Array.from(el.children)) observer.observe(child)
    return () => observer.disconnect()
  }, [version, nonce])
  const onScroll = () => {
    const el = ref.current
    if (!el) return
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
    measure()
  }
  return (
    <div className="chat__scrollwrap">
      {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- região rolável precisa de foco para uso por teclado (WCAG 2.1.1) */}
      <div ref={ref} className="chat__scroll" onScroll={onScroll} role="region" aria-label="Mensagens da conversa" tabIndex={0}>{children}</div>
      {!atBottom && <Button className="jump" icon="arrowDown" onClick={() => { stick.current = true; toEnd(true) }}>Ir para o fim</Button>}
    </div>
  )
}
