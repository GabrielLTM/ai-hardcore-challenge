import { useRef, useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import type { ApiProblem } from '../../shared/api'
import { qs, readPage } from '../../shared/api'
import { fmtDateTime } from '../../shared/format'
import { useDocumentTitle, usePolling, useResource } from '../../shared/hooks'
import { Chip, ProblemPanel, StatePanel } from '../../shared/ui'
import { elapsed, POLL_MS, reasonLabel, TABS, type ListTab, type Ticket } from './model'
import { opApi } from './session'

// a aba sobrevive à ida ao detalhe e à volta, sem ir para a URL
let lastTab: ListTab = 'abertos'

interface ViewProps {
  tab: ListTab
  onTab: (t: ListTab) => void
  items: Ticket[] | undefined
  problem?: ApiProblem
  onRetry: () => void
  now?: number
}

/** Abas + tabela. Separado da busca para ser renderizado nos testes com dados prontos. */
export function QueueView({ tab, onTab, items, problem, onRetry, now }: ViewProps) {
  const refs = useRef<Partial<Record<ListTab, HTMLButtonElement | null>>>({})
  const move = (e: KeyboardEvent<HTMLButtonElement>) => {
    const i = TABS.findIndex((t) => t.id === tab)
    const next = e.key === 'ArrowRight' ? TABS[(i + 1) % TABS.length] : e.key === 'ArrowLeft' ? TABS[(i + TABS.length - 1) % TABS.length]
      : e.key === 'Home' ? TABS[0] : e.key === 'End' ? TABS[TABS.length - 1] : null
    if (!next) return
    e.preventDefault()
    onTab(next.id)
    refs.current[next.id]?.focus()
  }
  const current = TABS.find((t) => t.id === tab) ?? TABS[0]
  const closed = tab === 'encerrados'
  const mine = tab === 'meus'
  const timeLabel = closed ? 'Duração' : mine ? 'Assumido há' : 'Esperando há'
  return (
    <section className="panel">
      <div role="tablist" aria-label="Filas de protocolos" className="op__tabs">
        {TABS.map((t) => (
          <button key={t.id} ref={(el) => { refs.current[t.id] = el }} type="button" role="tab" id={`aba-${t.id}`} className="tab"
            aria-selected={tab === t.id} aria-controls="fila" tabIndex={tab === t.id ? 0 : -1} onKeyDown={move} onClick={() => onTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="fila" aria-labelledby={`aba-${tab}`}>
        {problem && !items ? <div className="panel__body"><ProblemPanel problem={problem} what="os protocolos" onRetry={onRetry} /></div>
          : !items ? <div className="panel__body"><StatePanel kind="loading" what="os protocolos" /></div>
          : items.length === 0 ? <div className="panel__body"><StatePanel kind="empty" title={`Nenhum protocolo em ${current.label}`} message={current.empty} /></div>
          : (
            <div className="table-wrap">
              <table className="table op__row-table">
                <caption className="sr-only">Protocolos em {current.label}: protocolo, motivo e resumo, {timeLabel.toLowerCase()} e quem assumiu</caption>
                <thead><tr>
                  <th scope="col">Protocolo</th><th scope="col">Motivo</th><th scope="col">{timeLabel}</th><th scope="col">Quem assumiu</th>
                </tr></thead>
                <tbody>
                  {items.map((t) => (
                    <tr key={t.protocol} className="op__row">
                      <td data-label="Protocolo"><Link to={encodeURIComponent(t.protocol)} className="mono op__open">{t.protocol}</Link><div className="small muted">aberto em {fmtDateTime(t.created_at)}</div></td>
                      <td data-label="Motivo">{reasonLabel(t.reason)}{t.summary && <div className="small muted op__summary">{t.summary}</div>}</td>
                      <td data-label={timeLabel}>{elapsed(t, now, mine ? t.claimed_at : t.created_at)}</td>
                      <td data-label="Quem assumiu">{t.operator?.name ?? <Chip icon="clock">Ninguém ainda</Chip>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
    </section>
  )
}

export default function ListPage() {
  useDocumentTitle('Protocolos · Área do atendente')
  const [tab, setTabState] = useState<ListTab>(lastTab)
  const setTab = (t: ListTab) => { lastTab = t; setTabState(t) }
  // shortcut: mostra só os 100 primeiros por aba; usar next_cursor com "Carregar mais" se a fila crescer
  const list = useResource(`op-list:${tab}`, async (signal) => readPage<Ticket>(await opApi(`/handoffs${qs({ tab, limit: 100 })}`, { signal })).items)
  usePolling(list.reload, POLL_MS)
  return (
    <main id="main" tabIndex={-1} className="page op__page" aria-labelledby="op-list-t">
      <div className="page__head">
        <div>
          <h1 id="op-list-t">Protocolos</h1>
          <p>Conversas que o assistente passou para atendimento humano. A lista se atualiza sozinha.</p>
        </div>
      </div>
      <QueueView tab={tab} onTab={setTab} items={list.data} problem={list.problem} onRetry={list.reload} />
    </main>
  )
}
