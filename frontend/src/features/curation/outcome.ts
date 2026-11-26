/** What happened after the first verdict, in words: which version reached the customer. */
export function outcome(verdicts: string[], revision?: string | null): string {
  if (verdicts[1] === 'aprovar') return 'Nova versão aprovada e enviada'
  if (verdicts[1] === 'revisar') return 'Nova versão ainda com problemas, enviada assim'
  if (verdicts[1] === 'failed' || revision === 'failed') return 'A nova versão falhou; enviada a original'
  if (revision === 'skipped_budget') return 'Sem orçamento para nova versão; enviada a original'
  return 'Enviada a original'
}
