# Design

## Context

Motivação em `proposal.md`. Estado atual relevante:

- `MongoHandoffRepository.open` cria um ticket por execução em `handoffs` (`protocol`, `workspace_id`, `user_id`, `conversation_id`, `run_id`, `reason`, `summary`, `status: 'aberto'`), de forma idempotente. Nada lê nem altera o ticket depois disso.
- No perfil `local-demo`, toda requisição é a identidade de demonstração (`demo_identity`, papel `admin`); em `production`, a identidade vem do `OidcVerifier`. `Identity` já tem `role`.
- `begin_run` (usado por `POST /chat` e `POST /conversations/{id}/runs`) sempre cria uma execução. O chat do cliente só recebe conteúdo novo pelo SSE de uma execução.
- `conversation_history` envia ao Roteador as últimas mensagens `user`/`assistant` da conversa.
- `pyjwt` já é dependência (OIDC); a telemetria já define contadores e histogramas em `bootstrap/telemetry.py`, com dashboard provisionado por JSON.

## Goals / Non-Goals

**Goals:**
- Fechar o ciclo humano com o mínimo de peças novas, reaproveitando `handoffs`, `messages` e a identidade existente.
- Manter o isolamento por workspace e a regra de que a identidade do cliente nunca vem do chamador.

**Non-Goals:**
- Distribuição automática de protocolos, SLA, filas por habilidade, transferência entre atendentes, anexos.
- Tempo real por WebSocket ou SSE para mensagens humanas (fica polling).
- Cadastro de atendentes pela interface (vêm da carga de demonstração).
- Notificação ao cliente fora do chat (e-mail, push).

## Decisions

**1. Sessão de atendente: JWT HS256 assinado pelo servidor.** O login (`POST /api/v1/operator/login`, só em `local-demo`) confere a senha e devolve um JWT HS256 com `sub`, `name`, `workspace_id`, `role: attendant` e `exp` de 8 h, assinado com `OPERATOR_SESSION_SECRET`. Usa o `pyjwt` já instalado. Alternativa descartada: sessão em cookie com tabela no Mongo (mais estado, CSRF a tratar). Se o segredo não estiver configurado, a API gera um por processo e avisa no log: as sessões caem a cada reinício, aceitável na demo.

**2. Senhas com `hashlib.scrypt` da biblioteca padrão.** Sal por atendente, hash guardado em `operators`. Nenhuma dependência nova (bcrypt/argon2 descartados por isso). A senha de teste vem de `OPERATOR_DEMO_PASSWORD`, com valor de exemplo em `.env.example`; a carga de demonstração cria dois atendentes ("Ana", `ana@atendimento.demo`, e "Bruno", `bruno@atendimento.demo`) para mostrar o conflito ao assumir. `GET /api/v1/operator/demo-accounts` (só `local-demo`) devolve nome, e-mail e senha de teste, e a tela de login mostra um botão por conta que preenche os campos. Senha exposta de propósito: é credencial de teste, e a rota some fora do perfil de demonstração. Alternativa descartada: fixar as contas no build do frontend, que ficaria fora de sincronia com o `.env`.

**3. Dependência `current_operator` separada de `current_identity`.** Rotas `/api/v1/operator/*` usam só `current_operator`: em `local-demo` valida o JWT de atendente; em `production` usa o `OidcVerifier` e exige o papel `attendant` (`OIDC_ATTENDANT_ROLE`). O papel `operator` já existia e é o cliente comum em produção, por isso o atendente ganhou um papel próprio. As rotas do cliente continuam com `current_identity`, que recusa com `403` a sessão de atendente (em `local-demo`, o JWT HS256 da área; em produção, o papel `attendant`). O atendente opera com um `Identity` próprio; para ler dados do cliente, o servidor monta o `Identity` do cliente a partir de `workspace_id`/`user_id` do protocolo.

**4. O protocolo é a fonte única do modo humano.** Não há campo novo em `conversations`: a conversa está em modo humano se existe protocolo `aberto` ou `em_atendimento` para ela. Garantido por índice único parcial em `handoffs` sobre `(workspace_id, conversation_id)` com `active: true` (campo removido ao encerrar). `open` passa a devolver o protocolo ativo da conversa, se houver (requisito "um protocolo ativo por conversa"). Alternativa descartada: flag em `conversations`, que duplicaria estado e exigiria transação para manter os dois coerentes.

**5. Desvio no ponto de entrada.** `begin_run` consulta o protocolo ativo antes de `create_run`; se existir, grava a mensagem do cliente em `messages` (sem `run_id`), acrescenta o evento à trilha e devolve `{status: 'with_operator', protocol, handoff_status}`. `POST /chat` responde `200` com `answer: null`; `POST /conversations/{id}/runs` responde `202` com o mesmo corpo e sem `events_url`. Nenhum custo de modelo.

**6. Estados por atualização atômica.** Assumir: `find_one_and_update({protocol, workspace_id, status: 'aberto'}, {status: 'em_atendimento', operator: {id, name}, claimed_at})`; resultado vazio vira `409` com o nome de quem tem o protocolo. Encerrar: mesmo padrão com filtro `status: 'em_atendimento', operator.id == eu`, gravando `resolution`, `closed_at` e removendo `active`. Cada ação faz `$push` em `timeline` (`{kind, operator, at, ...}`) na mesma atualização, então trilha e estado não divergem.

**7. Mensagens novas na conversa.** `messages` ganha o papel `operator` (com `author_name`) e `system` (avisos "entrou na conversa" e "atendimento encerrado"). Notas internas ficam só em `handoffs.timeline` (`kind: 'note'`), nunca em `messages`. `GET /conversations/{id}/messages` passa a devolver também `handoff: {protocol, status, operator_name} | null`, que é o que o chat do cliente usa para decidir o aviso e o polling.

**8. Histórico do Roteador.** `conversation_history` passa a incluir mensagens `operator` como `role: assistant` com o prefixo `[Atendente <nome>]`, e ignora `system`. O Roteador entende que aquela fala foi humana sem um papel novo na API do modelo.

**9. Polling de 3 s nos dois lados.** O chat do cliente consulta as mensagens enquanto `handoff` não for nulo; a área do atendente consulta a lista e o detalhe aberto. Alternativa descartada: SSE por conversa, que exigiria um canal novo fora do modelo de execução. Volume de demo é baixo; ver riscos.

**10. Métricas.** Em `telemetry.py`: contador `getnet.handoffs` por evento (`opened`, `claimed`, `closed`) e histogramas `getnet.handoff.wait` (abertura → assunção) e `getnet.handoff.handle` (assunção → encerramento). "Abertos agora" no painel é `opened − closed` acumulado. Sem atributos de cliente, protocolo ou texto.

## Risks / Trade-offs

- [Cliente preso em "Aguardando um atendente" se ninguém assumir] → a lista ordena por espera e o painel mostra o tempo até assumir; SLA e expiração ficam fora do escopo.
- [Polling gera carga com muitos clientes em modo humano] → intervalo fixo de 3 s, só enquanto há protocolo ativo; trocar por SSE por conversa se a medição pedir.
- [`opened − closed` diverge se a API reiniciar com protocolos abertos] → o número exato está na aba Abertos; o painel é tendência.
- [Segredo de sessão gerado por processo derruba sessões a cada reinício] → configurar `OPERATOR_SESSION_SECRET` no `.env`; documentado.
- [Mensagem do cliente chega durante a execução que abriu o protocolo] → a execução ativa já bloqueia nova execução na conversa; ao terminar, o protocolo existe e o desvio vale para a próxima.
- [Atendente vê dados de suporte do cliente] → só do cliente do protocolo, só leitura, mesmo workspace; nada é enviado ao modelo.

## Migration Plan

Sem migração de dados: tickets antigos (`status: 'aberto'`, sem `active`) não colocam a conversa em modo humano; a carga de demonstração marca `active: true` nos que criar. Índices novos são criados no `ensure_indexes` da subida. Rollback: reverter o deploy; os campos novos são ignorados pela versão anterior.
