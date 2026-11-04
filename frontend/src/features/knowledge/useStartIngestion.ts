import { useState } from 'react'
import { api, toProblem, type ApiProblem } from '../../shared/api'
import { useToast } from '../../shared/toast-context'
import type { Job } from './model'

export function useStartIngestion(onStarted?: (job: Job) => void) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<ApiProblem | null>(null)
  const [started, setStarted] = useState<Job | null>(null)
  const start = async (sourceIds?: string[]) => {
    setBusy(true); setProblem(null); setStarted(null)
    try {
      const job = await api<Job>('/knowledge/ingestions', { method: 'POST', body: sourceIds?.length ? { source_ids: sourceIds } : undefined })
      toast('Ingestão iniciada. Acompanhe as etapas nesta página.')
      setStarted(job)
      onStarted?.(job)
      return job
    } catch (e) { setProblem(toProblem(e)); return null } finally { setBusy(false) }
  }
  return { start, busy, problem, started, clear: () => setProblem(null) }
}

