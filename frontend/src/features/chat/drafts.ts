/** Rascunho do campo de mensagem por conversa, só na sessão do navegador (sobrevive a falhas e a 401). */
const draftKey = (id?: string) => `trilha:draft:${id ?? 'new'}`
export const readDraft = (id?: string) => { try { return sessionStorage.getItem(draftKey(id)) ?? '' } catch { return '' } }
export const saveDraft = (id: string | undefined, value: string) => {
  try { if (value) sessionStorage.setItem(draftKey(id), value); else sessionStorage.removeItem(draftKey(id)) } catch { /* sem armazenamento */ }
}

