# Contrato de experiência

Fonte de comportamento: especificações OpenSpec `frontend-workspace`, `chat-streaming-history`, `agent-configuration` e `knowledge-retrieval`; visual em `DESIGN.md`. Código em `frontend/src` (`shared/` componentes, `features/chat|agents|knowledge`, `app/` shell e rotas).

## Execução local

- `cd frontend && npm install && npm run dev` serve em `http://localhost:5173`; o Vite encaminha `/api` para `http://localhost:8000` (altere com `VITE_API_TARGET`). SSE passa pelo mesmo proxy.
- Variáveis opcionais: `VITE_TIMEZONE` (padrão `America/Sao_Paulo`) e `VITE_DEMO_USER_ID` (padrão `cliente1988`, usado só se a conversa não informar `user_id`).
- Verificação: `npm run build` (tipagem + bundle), `npm run lint` (oxlint com `jsx-a11y`) e `npm test` (vitest: eventos, evidências, Markdown, diff, formatação).

## Rotas

`/conversas[/:id]`, `/agentes`, `/agentes/:id`, `/agentes/:id/versoes`, `/base`, `/base/:fonte`. Nenhum segredo, termo de busca ou conteúdo de mensagem vai para a URL ou query string; filtros e buscas vivem em estado.

## Chat

- Uma execução ativa por conversa; outras conversas continuam navegáveis (a execução segue no servidor, a tela só assina os eventos).
- Composer: Enter envia, Shift+Enter quebra linha, composição por IME (`isComposing`/keyCode 229) nunca envia. Enviar fica desabilitado durante a execução e quando offline. O texto digitado é guardado em `sessionStorage` por conversa e só sai do campo quando o servidor aceita a execução.
- `POST /conversations/{id}/runs` leva `Idempotency-Key`. A mesma chave só é reutilizada no retry da mesma intenção (mesmo texto, mesma conversa, após falha ao enviar). "Enviar novamente", "Tentar novamente" e "Iniciar nova execução" depois de uma execução terminar criam nova intenção e nova chave, pois o servidor devolveria o mesmo run já terminado.
- Estados da resposta: aguardando, roteando, executando, gerando, concluída, falhou, cancelada, interrompida e reconectando. Cada um tem texto visível.
- Streaming por `EventSource` em `/runs/{id}/events?after=<seq>`. Cada `seq` entra uma vez (replay seguro). Em queda, fecha e reabre a partir do último `seq` com recuo exponencial, mostrando "Reconectando…" e "Tentar agora"; o texto recebido nunca some. Um vigia consulta `GET /runs/{id}` após silêncio de 15s (o EventSource não expõe heartbeats). Execução terminal consultada fora do stream usa o estado de `GET /runs/{id}`. 410 mostra "Eventos expirados" e o estado final.
- Parar chama `POST /runs/{id}/cancel`, mantém o texto parcial rotulado "Resposta parcial: interrompida por você" e preserva o rascunho do campo.
- Rolagem acompanha só quando o usuário está no fim; senão aparece "Ir para o fim". `aria-live="polite"` cobre etapas e blocos concluídos, nunca tokens (o texto em crescimento é `aria-live="off"`).
- Markdown renderizado como elementos React (sem HTML bruto): parágrafos, listas, citações, código, negrito, itálico, links `http(s)`/`mailto` com `rel="noopener noreferrer"` e `[n]` como botão que abre a fonte.
- Erros de envio ficam em faixa persistente com `request_id` e "Tentar novamente". Falha de execução mostra o motivo sanitizado do backend.

## Andamento e Fontes

- Passos nascem de `route.selected`, `agent.*`, `tool.*`, `retrieval.completed` e `usage.recorded`, em linguagem de atendimento. Raciocínio interno nunca é mostrado; argumentos e dados de ferramentas chegam mascarados do backend.
- "Detalhes técnicos" (recolhido): execução, `trace_id` com copiar, início em BRT e UTC, duração, tokens, custo (`cost_usd` somado quando informado, "não informado" quando não há preço, nunca 0), agentes com versão e grafo, ferramentas com duração.
- Fontes vêm de `GET /runs/{id}/evidence`: "Usadas na resposta" (citadas, com `[n]`) e "Consultadas, mas não usadas" (borda tracejada). Cartão: tipo, trecho, limitações, data de coleta, "Abrir original" e detalhes (endereço, índice, idioma).

## Agentes

- Lista com busca (com limpar), filtros tipo/status, paginação por cursor e "Criar agente". Rotas restritas a administrador: 403 mostra "Acesso restrito".
- Editor: rascunho com `revision`; salvar envia `If-Match: "<revision>"`. "Validar relações" mostra o caminho do ciclo e foca o primeiro vínculo inválido; Publicar valida de novo e abre confirmação quando muda ferramentas ou vínculos (marcação obrigatória quando concede algo novo). `delegate_to_agent` é derivado: existe quando há agentes delegáveis.
- Conflito 412: diálogo com "Sua edição" e "Rev. N (atual)" lado a lado, copiar minha edição, manter minha edição sobre a nova revisão ou recarregar. O texto digitado nunca é descartado sem ação explícita. `beforeunload` avisa sobre alterações não salvas.
- Vínculos diretos com alternativa textual. Versões: lista, diff legível (servidor, ou local quando envolve o rascunho), prompts e snapshots do grafo; restaurar cria nova versão com confirmação.

## Base de conhecimento

- Lista de fontes com tipo, URL, coleta, status e última indexação; detalhe com metadados, limitações, trechos e histórico. Fonte com falha mantém a versão indexada anterior e diz isso.
- Reprocessar (selecionadas ou todas) cria `POST /knowledge/ingestions` e acompanha `GET /knowledge/ingestions/{id}` a cada 2s enquanto estiver na fila ou rodando. Fases reais: coletar, extrair, dividir, embeddings, indexar, com contagem de fontes e sem percentual. Falha indica a etapa e oferece tentar de novo as fontes com falha.

## Estados globais

Carregando (esqueleto com texto para leitor de tela), vazio, sem resultado (com limpar busca), falha (com `request_id`), 401 (entrar novamente; rascunho preservado), 403, timeout, 409/412, offline (faixa global via `navigator.onLine`, Enviar desabilitado) e autorização revogada (conteúdo oculto). Erros da API chegam como `{code, message, request_id, details?}`; erros de validação 422 viram erros por campo.

## Acessibilidade e responsividade

- WCAG 2.2 AA: foco visível, rótulos associados, `aria-current`, tabs com setas, tabelas com `caption`, link "Pular para o conteúdo", foco levado ao conteúdo quando a página muda, alvos de 44px em toque, contraste mínimo `ink-3`.
- Diálogos, drawers e sheets: foco preso e restaurado, Esc fecha, fundo inerte.
- Movimento reduzido desliga animações. Zoom de 200% reflui para o layout estreito. Celular: histórico e painéis são drawer/sheet que não bloqueiam a execução do chat.
- Barras de rolagem: estilo global em `frontend/src/index.css` (`scrollbar-color`); nunca ocultas.
- Data e hora: pt-BR em `America/Sao_Paulo`, instantes da API em UTC; datas sem horário (ex.: `retrieved_on`) não sofrem deslocamento de fuso.
