import { useRef, useState, type KeyboardEvent } from 'react'
import { Button } from '../../shared/ui'
import { readDraft, saveDraft } from './drafts'

interface Props {
  conversationId?: string
  busy: boolean        // há execução ativa: Enviar fica desabilitado e Parar aparece
  stopping: boolean
  offline: boolean
  /** Resolve true quando a execução foi aceita; em falha o texto permanece no campo. */
  onSend: (text: string) => Promise<boolean>
  onStop: () => void
}

/** Enter envia, Shift+Enter quebra linha e a composição por IME (isComposing) nunca envia. O rascunho sobrevive a falhas e a 401. */
export function Composer({ conversationId, busy, stopping, offline, onSend, onStop }: Props) {
  const [text, setText] = useState(() => readDraft(conversationId))
  const [sending, setSending] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  const canSend = text.trim().length > 0 && !busy && !offline && !sending

  const change = (value: string) => {
    setText(value)
    saveDraft(conversationId, value)
    const el = ref.current
    if (el) { el.style.height = 'auto'; el.style.height = `${Math.min(el.scrollHeight + 2, 200)}px` }
  }
  const submit = async () => {
    if (!canSend) return
    setSending(true)
    const ok = await onSend(text.trim())
    setSending(false)
    if (ok) { setText(''); saveDraft(conversationId, ''); if (ref.current) ref.current.style.height = '' }
  }
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing || e.keyCode === 229) return
    e.preventDefault()
    void submit()
  }

  return (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); void submit() }}>
      <label htmlFor="mensagem" className="sr-only">Mensagem</label>
      <textarea id="mensagem" ref={ref} className="textarea" rows={3} value={text} placeholder="Escreva uma mensagem"
        aria-describedby="dica-composer" onChange={(e) => change(e.target.value)} onKeyDown={onKeyDown} />
      <div className="composer__bar">
        <span id="dica-composer" className="muted small">
          {offline ? 'Você está offline: o envio volta quando a conexão voltar.' : 'Enter envia · Shift+Enter quebra linha'}
        </span>
        <div className="composer__actions">
          {busy && <Button variant="danger" icon="stop" busy={stopping} onClick={onStop}>{stopping ? 'Parando…' : 'Parar'}</Button>}
          <Button variant="primary" type="submit" disabled={!canSend} busy={sending}>Enviar</Button>
        </div>
      </div>
    </form>
  )
}
