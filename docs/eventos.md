# Eventos de execução (SSE)

Cada execução (`run`) produz uma sequência persistida de eventos. O cliente a acompanha em `GET /api/v1/runs/{run_id}/events` (`text/event-stream`) e pode reconectar a partir do último evento recebido. Os exemplos abaixo vêm de uma execução real do dia 03/11/2026 ("Minha maquininha está sem internet…", `run_id` abreviado onde indicado) e dos testes de contrato `backend/tests/test_core_api.py`, `test_core_errors.py` e `test_core_orchestrator.py`. Para as demais rotas, veja [api.md](api.md).

## Formato do quadro

```text
id: 2
event: agent.started
data: {"run_id": "720ee154bf184368b543c48cc18cadb6", "seq": 2, "occurred_at": "2026-11-04T02:11:17.330000Z", "type": "agent.started", "agent_id": "router", "payload": {"agent_id": "router", "name": "Roteador", "version": 2, "parent_agent_id": null}}

```

- `id` é o `seq` do evento: inteiro que começa em 1 e cresce de um em um, único por execução, sem buracos enquanto o evento existir.
- `event` repete o tipo; `data` é um JSON de uma linha (UTF-8, `ensure_ascii` desligado) com `run_id`, `seq`, `occurred_at` (UTC, RFC 3339 com `Z`), `type`, `agent_id` (só quando o `payload` tem um) e `payload`.
- `workspace_id` e `user_id` nunca aparecem no envelope.
- Comentários `: heartbeat` (linha iniciada por `:`) mantêm a conexão viva quando não há eventos novos por `SSE_HEARTBEAT_SECONDS` (15 s). Ignore-os.
- O stream termina sozinho depois do evento terminal. Fechar a conexão do cliente **não** cancela a execução.
- Cabeçalhos: `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`, e `X-Request-ID`.

Os quadros podem chegar cortados em qualquer byte, inclusive no meio de um caractere multibyte; o separador é a linha em branco (`\n\n`). Um decodificador UTF-8 incremental acumulando o buffer e separando por `\n\n` reconstrói os quadros (coberto por `test_sse_contract_replay_without_duplicates_and_fragmented_utf8`).

## Tipos de evento

Todos os `payload` são sanitizados: sem raciocínio do modelo, sem credenciais, e com números longos (CPF, CNPJ, cartão, conta) mascarados como `[mascarado]` nos argumentos das ferramentas, que também são truncados em 200 caracteres. Trechos recuperados nunca viajam nos eventos, só identificadores e metadados.

| Tipo | Quando | Campos do `payload` |
|---|---|---|
| `run.started` | Início da execução | `graph_version` (versão do grafo fixada) |
| `agent.started` | Um agente começa | `agent_id`, `name`, `version`, `parent_agent_id` (`null` no roteador) |
| `agent.completed` | Um agente termina | `agent_id`, `version` |
| `agent.failed` | Um agente falha por violação de regra ou limite | `agent_id`, `version`, `code`, `message` |
| `route.selected` | O servidor aceitou uma delegação | `agent_id` (origem), `destination` |
| `tool.started` | Uma ferramenta vai executar | `agent_id`, `tool`, `arguments` (`{agent_id, task}` na delegação; `{query}` nas buscas; `{}` nas ferramentas de suporte) |
| `tool.completed` | A ferramenta terminou | `agent_id`, `tool`, `summary` (`Resposta de <agente>`, `N trechos recuperados`, `N fontes`, `N registros`, `Concluída`) |
| `tool.failed` | A ferramenta foi recusada ou falhou | `agent_id`, `tool`, `code`, `message` |
| `retrieval.completed` | Busca na base ou na web trouxe resultados | `agent_id`, `source` (`knowledge` ou `web`), `results`: itens `{evidence_id, kind, title, url, retrieved_on}` (base) ou `{evidence_id, title, url}` (web) |
| `response.delta` | Pedaço da resposta final (só o agente raiz, em lotes de ~200 caracteres ou 0,25 s) | `text`, `agent_id` |
| `usage.recorded` | Uma chamada ao modelo terminou | `agent_id`, `model`, `usage` (como o provedor informou), `cost_usd` (`null` para modelo fora da tabela), `pricing_version` |
| `run.completed` | Terminal: resposta concluída | `cited_evidence` (ids citados e válidos), `invalid_citations` |
| `run.failed` | Terminal: falha | `cited_evidence`, `invalid_citations`, `code`, `message` |
| `run.cancelled` | Terminal: cancelada a pedido | `cited_evidence`, `invalid_citations` |
| `run.interrupted` | Terminal: a execução parou sem terminar | `code` (`interrupted` ou `lease_expired`), `message`, e `cited_evidence`/`invalid_citations` quando a instância dona a encerrou |

Códigos de `tool.failed`: `tool_not_allowed`, `invalid_arguments`, `delegation_not_allowed`, `limit_delegations`, `limit_tool_calls`, `tool_unavailable` (exceção do banco ou do provedor) e `unavailable` (busca web sem resposta). Códigos de `agent.failed`/`run.failed`: `delegation_invalid`, `delegation_cycle`, `limit_depth`, `limit_output_tokens`, `limit_timeout`, `model_error`, `no_router`, `internal_error`. Um estado terminal (`run.*`) aparece exatamente uma vez por execução e é sempre o último evento.

Exemplos reais, em sequência (campos do envelope omitidos nos três primeiros por brevidade):

```text
id: 1  event: run.started
  payload: {"graph_version": "4"}

id: 4  event: tool.started
  payload: {"agent_id": "router", "tool": "delegate_to_agent", "arguments": {"agent_id": "support", "task": "O cliente pergunta: “Minha maquininha está sem internet…"}}

id: 5  event: route.selected
  payload: {"agent_id": "router", "destination": "support"}

id: 16  event: retrieval.completed
  payload: {"agent_id": "knowledge", "source": "knowledge", "results": [{"evidence_id": 1, "kind": "official", "title": "Dúvidas frequentes (página oficial)", "url": "https://site.getnet.com.br/duvidas/", "retrieved_on": "2026-11-04"}, …]}

id: 18  event: usage.recorded
  payload: {"agent_id": "knowledge", "model": "gpt-6-luna", "usage": {"input_tokens": 2907, "input_tokens_details": {"cache_write_tokens": 1734, "cached_tokens": 1170}, "output_tokens": 86, "output_tokens_details": {"reasoning_tokens": 24}, "total_tokens": 2993}, "cost_usd": null, "pricing_version": "2026-11-03.1"}

id: 38  event: run.completed
  payload: {"cited_evidence": [1, 6], "invalid_citations": []}
```

`usage.recorded` informa só a contagem `reasoning_tokens`, nunca o raciocínio. `cost_usd: null` é "não informado" (o modelo `gpt-6-luna` não tem preço na tabela `PRICING_VERSION`), nunca zero. Os tokens da consulta de embedding e da chamada de busca web não geram `usage.recorded` hoje.

A ordem típica de uma execução com delegação é `run.started`, `agent.started` (roteador), `usage.recorded`, `tool.started` (`delegate_to_agent`), `route.selected`, `agent.started` (especialista), ferramentas e `retrieval.completed`, `agent.completed`, `tool.completed`, mais `usage.recorded` do roteador, `response.delta` (vários), `agent.completed` e o evento terminal. A execução de 38 eventos acima delega duas vezes (`router → support → knowledge`).

## Texto da resposta

Concatenar os `payload.text` de todos os `response.delta`, na ordem de `seq`, reproduz exatamente a resposta (cada caractere uma vez, com acentos e emoji intactos); o mesmo texto fica em `GET /runs/{id}` (`partial_text`) e na mensagem do assistente. O contrato é verificado com texto UTF-8 de vários bytes dividido em lotes (`test_sse_contract_replay_without_duplicates_and_fragmented_utf8`). Se a resposta contiver `[n]`, `n` aponta para `evidence_id` em `GET /runs/{id}/evidence`.

## Retomada (`Last-Event-ID` e `?after=`)

Para reconectar, envie o último `seq` recebido de duas formas equivalentes:

```bash
# cabeçalho (o EventSource do navegador o envia sozinho ao reconectar)
curl -N -H 'Last-Event-ID: 36' http://localhost:8000/api/v1/runs/$RUN_ID/events

# parâmetro de consulta (o EventSource reconecta na mesma URL original, então o cabeçalho tem prioridade)
curl -N "http://localhost:8000/api/v1/runs/$RUN_ID/events?after=36"
```

Resposta observada na execução real acima, com `Last-Event-ID: 36`: os quadros `37` (`agent.completed`) e `38` (`run.completed`). Regras:

- Sem cursor (ou com cursor vazio ou `0`) o stream começa no evento 1; se os primeiros eventos já expiraram, vale a regra do 410 abaixo.
- `Last-Event-ID` não vazio vence `?after=` (`?after=0` com `Last-Event-ID: 37` entrega só o `38`).
- O cursor é o último `seq` já recebido: o servidor envia os eventos com `seq` maior. Um cursor igual ao último `seq` de uma execução terminada devolve `200` com corpo vazio.
- Um cursor que não é inteiro não negativo de até 18 dígitos (`abc`, `-1`, `1.5`) responde **400** `invalid_cursor` (“Cursor inválido: informe o último seq recebido.”). Um cursor à frente do último evento (`after=9999`) também é **400** (“Cursor à frente do último evento da execução.”). Ambos chegam antes de o stream abrir, como JSON (`application/json`).
- Reconectar em outra instância da API funciona: o stream lê o banco compartilhado, sem estado em memória.

Sem duplicatas: depois de reconectar com o último `seq` recebido, os `seq` seguintes são contíguos (`cut+1 … n`).

### 410: cursor expirado

Os eventos são mantidos por `EVENT_RETENTION_DAYS` (padrão 7 dias) por um índice TTL em `run_events.occurred_at`. Se há eventos a entregar e o cursor é menor que `first_available_seq - 1` (o primeiro evento ainda guardado menos um), a API responde, antes de abrir o stream (corpo conforme o teste de contrato; `request_id` ilustrativo):

```json
HTTP/1.1 410 Gone
{
  "code": "cursor_expired",
  "message": "Os eventos desse cursor expiraram; consulte a execução e o histórico da conversa.",
  "request_id": "8d3612fd289f4158a12325842f88a18f",
  "details": {"run_id": "720ee154bf184368b543c48cc18cadb6", "first_available_seq": 4}
}
```

O cliente deve então ler o estado final em `GET /runs/{run_id}` e `GET /conversations/{id}/messages`, que continuam disponíveis depois da expiração dos eventos. O teste `test_cursor_older_than_retention_is_410_before_the_stream_opens` apaga os eventos `seq ≤ 3` (o que o TTL faz) e confirma: `after=0` e `after=2` são 410 com `first_available_seq: 4`; `Last-Event-ID: 3` entrega a partir do `4`; sem nenhum evento, `after=0` é 410 e a execução segue `completed`. Para mudar a retenção, defina `EVENT_RETENTION_DAYS`; a API atualiza o índice TTL na inicialização (`collMod`).

## Estado e ciclo de vida

`GET /runs/{run_id}` é a fonte autoritativa. Campos: `id`/`run_id`, `conversation_id`, `status`, `graph_version`, `trace_id`, `partial_text`, `sources`, `usage` (`input_tokens`, `output_tokens`, `total_tokens`, `cost_usd`, `pricing_version`), `error`, `cancel_requested`, `seq`, `created_at`, `updated_at`, `final` (verdadeiro só em `completed`) e `events_url`. Estados: `queued`, `running` (ativos) e `completed`, `failed`, `cancelled`, `interrupted` (terminais).

### Cancelamento

`POST /api/v1/runs/{run_id}/cancel` é idempotente e responde com o estado real, que pode ainda ser `running` com `cancel_requested: true`:

- Se a execução roda nesta instância, a tarefa é cancelada na hora e a resposta espera até `CANCEL_WAIT_SECONDS` (2 s).
- Se roda em outra instância, o pedido fica no banco e o dono o percebe no próximo heartbeat (`RUN_HEARTBEAT_SECONDS`, 3 s).
- O texto já gerado fica salvo: o evento terminal é `run.cancelled`, `partial_text` mantém o parcial (`Olá mundo` no teste) e a mensagem do assistente tem `status: cancelled`.
- Cancelar uma execução já terminada devolve o estado terminal e não cria `run.cancelled`. Na corrida cancelamento × conclusão, o primeiro estado terminal persistido vence e há exatamente um evento `run.*` terminal.
- O cancelamento não promete estorno nem interrupção imediata da chamada ao provedor.

### Lease e `interrupted`

Quem executa renova um lease (`RUN_LEASE_SECONDS`, 30 s) a cada `RUN_HEARTBEAT_SECONDS`. Uma varredura periódica (`MAINTENANCE_INTERVAL_SECONDS`, 10 s) em qualquer instância marca como `interrupted` as execuções ativas com lease vencido (instância caiu ou travou):

```text
id: 2
event: run.interrupted
data: {"run_id": "…", "seq": 2, "occurred_at": "…Z", "type": "run.interrupted", "payload": {"code": "lease_expired", "message": "A execução foi interrompida antes de concluir; envie novamente."}}
```

O parcial e os eventos ficam preservados, a mensagem do assistente recebe `status: interrupted`, a conversa é liberada e a API **não** retoma nem repete a chamada ao modelo: o usuário envia de novo com outra chave (`test_expired_lease_marks_run_interrupted_keeping_partial_and_never_regenerates`). Quando a própria API é encerrada, as execuções locais terminam como `interrupted` com `code: interrupted`.

## Idempotência

`POST /conversations/{conversation_id}/runs` exige o cabeçalho `Idempotency-Key` (1 a 200 caracteres) e responde `202` com `{conversation_id, run_id, status, events_url}`:

| Situação | Resposta |
|---|---|
| Mesma chave e mesmo corpo (antes ou depois do término) | `202` com o mesmo `run_id`; nenhuma nova execução, nem nova chamada ao modelo |
| Mesma chave, mensagem diferente | `409` `idempotency_conflict` |
| Outra chave, com a conversa ainda executando | `409` `conversation_busy` |
| Conversa inexistente ou de outro usuário | `404` `conversation_not_found` |
| Limite de execuções ativas do workspace | `429` `capacity_exceeded` com `Retry-After: 5` |

Cinco requisições simultâneas com a mesma chave criam um único run (as outras repetem o vencedor). `POST /chat` usa uma chave nova a cada chamada: repetir a mesma mensagem cria outra execução.

## Exemplo completo com `curl`

Requer `OPENAI_API_KEY` configurada (cada execução consome tokens do provedor).

```bash
CONV=$(curl -s -X POST localhost:8000/api/v1/conversations -H 'Content-Type: application/json' \
  -d '{"title":"Teste de eventos"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

RUN=$(curl -s -X POST localhost:8000/api/v1/conversations/$CONV/runs \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-001' \
  -d '{"message":"Qual o prazo do Pix?","user_id":"cliente1988"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["run_id"])')

curl -N localhost:8000/api/v1/runs/$RUN/events          # acompanha até o evento terminal
curl -s -X POST localhost:8000/api/v1/runs/$RUN/cancel   # opcional: pede cancelamento
curl -s localhost:8000/api/v1/runs/$RUN/evidence         # trechos recuperados e citados
```

No navegador, `EventSource` basta: ele reconecta sozinho com `Last-Event-ID`. O frontend desta solução (`frontend/src/features/chat/useRunStream.ts`) usa `EventSource`, reconecta manualmente com `?after=<último seq>` (a URL nativa guardaria o cursor antigo) e consulta `GET /runs/{id}` ao receber 410.
