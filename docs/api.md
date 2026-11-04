# API HTTP

Referência dos endpoints, da configuração dos provedores e dos dados de demonstração. A API completa está no OpenAPI gerado pelo código; os exemplos abaixo foram conferidos contra ele (seção "Como os exemplos foram validados"). Eventos SSE e retomada ficam em [eventos.md](eventos.md).

- Base: `http://localhost:8000/api/v1` (Compose publica a porta só em `127.0.0.1`). Também em `http://localhost:8080/api/v1` pelo proxy do serviço `frontend`.
- OpenAPI e documentação interativa: `http://localhost:8000/openapi.json` e `http://localhost:8000/docs`.
- Datas em UTC, RFC 3339 com `Z`. IDs gerados pelo servidor. Mensagens ao usuário em pt-BR.
- Cada resposta traz `X-Request-ID` (aceita um valor seu de 8 a 64 caracteres `[A-Za-z0-9._-]` e o devolve). O `trace_id` (id de correlação por execução) vem no corpo de `POST /chat` e de `GET /runs/{id}`.

## Identidade (perfil `local-demo`)

Não há login no perfil local: toda requisição age como o usuário `DEMO_USER_ID` (`cliente1988`) no workspace `DEMO_WORKSPACE_ID` (`demo`) com papel `admin`; o cabeçalho `Authorization` é ignorado. O `user_id` do corpo de `/chat` e `/runs` é só uma declaração do cliente e precisa ser o usuário autenticado (no perfil local, esse valor); qualquer outro responde `403 forbidden`. O backend, não o modelo, fornece a identidade às ferramentas. As rotas de administração (`/agents`, `/prompts`, `/graph`, `/knowledge`) exigem papel `admin`.

No perfil `production` toda rota `/api/v1` (exceto a saúde) exige `Authorization: Bearer <JWT OIDC>` (o OpenAPI declara o esquema `HTTPBearer`): token ausente, inválido ou expirado responde `401 unauthorized` com `WWW-Authenticate`; token válido sem workspace ou papel utilizável é `403`; provedor de identidade indisponível é `503 service_unavailable` com `Retry-After`. Variáveis, mapeamento de claims e testes em [producao.md](producao.md).

## Configuração dos provedores

Tudo vem de `.env` na raiz (modelo em [`.env.example`](../.env.example); nunca commite o arquivo real).

**OpenAI (modelos de linguagem).** `OPENAI_API_KEY` (guardada como segredo; não aparece em `repr`, logs, eventos nem respostas) e `OPENAI_MODEL` (padrão `gpt-6-luna`, usado nos agentes criados pelo seed e na busca web; cada agente pode ter outro modelo em seu cadastro). A API usa a Responses API em streaming com funções estritas. Sem a chave, a API sobe normalmente, mas `POST /chat` e `POST /conversations/{id}/runs` respondem `503 provider_not_configured`; leitura de histórico, agentes e base continua funcionando.

**Embeddings.** `EMBEDDING_MODEL` (padrão `text-embedding-3-small`, 1536 dimensões) vale para a ingestão e para a consulta. O índice vetorial é criado com essa dimensão. Para outro modelo fora de `text-embedding-3-small/large` e `ada-002`, informe `KNOWLEDGE_EMBEDDING_DIMENSIONS`. A busca filtra por `embedding_model`: trocar de modelo exige reprocessar a base (`POST /knowledge/ingestions`) e recriar o índice vetorial; a criação do índice recusa dimensão divergente em vez de misturar vetores.

**Busca web.** A ferramenta `web_search` chama a Responses API com a ferramenta hospedada `web_search` (modelo `OPENAI_MODEL`, timeout fixo de 90 s; a execução inteira tem `RUN_TIMEOUT_SECONDS`, então use pelo menos 180 s quando a busca for usada) e devolve `{status, answer, sources, retrieved_at}`. Qualquer falha (timeout, erro do provedor, resposta vazia) vira `status: "unavailable"` com fontes vazias; o agente informa a indisponibilidade sem inventar. Fontes sem URL (por exemplo, o serviço de dados do clima, `oai-weather`) aparecem com título e `url: null`. A busca hospedada é cobrada por chamada pela OpenAI, além dos tokens; o aplicativo não estima esse valor nem registra os tokens dessa chamada. Consulte a página oficial de preços da OpenAI.

**Custo por chamada.** Cada chamada de modelo gera um `usage.recorded` com o uso informado pelo provedor, `cost_usd` e `pricing_version` (hoje `2026-11-04.1`). A estimativa usa a tabela `domain/pricing.py` (USD por 1 milhão de tokens, com tokens em cache a preço próprio quando houver). A tabela traz `gpt-6-luna` (US$ 0,10 entrada, US$ 0,50 saída e US$ 0,01 entrada em cache por 1M tokens, referência 17/10/2026), `text-embedding-3-small` e `gpt-4o-mini`; confirme os valores na página oficial. Modelo fora da tabela resulta em `cost_usd: null` ("não informado"), nunca em zero; a execução (`usage.cost_usd`) também fica `null` se alguma chamada não tiver preço. A cobrança do provedor é a fonte da verdade.

## Dados de demonstração do suporte

As ferramentas de suporte leem fixtures marcadas como fictícias (`is_demo: true`), sem integração com sistemas da Getnet. A API as recria a cada inicialização (data de venda = ontem, previsão = hoje + 29 dias, calendário de Brasília) para que "ontem" continue sendo ontem.

`get_receivables` para `cliente1988` (valores observados em 03/11/2026):

```json
{
  "is_demo": true,
  "items": [
    {"sale_date": "2026-11-02", "due_date": "2026-12-02", "amount": "3482.90", "status": "previsto", "payment_type": "credito_a_vista"}
  ],
  "notice": "Dados fictícios de demonstração, sem integração com sistemas reais da Getnet.",
  "currency": "BRL"
}
```

`get_terminal_status`:

```json
{
  "is_demo": true,
  "items": [
    {"model": "Get Smart (série final 4821)", "connectivity": "sem sinal da operadora (chip Algar)", "error": "Sem sinal da rede móvel",
     "serial_suffix": "4821", "carrier": "Algar", "carrier_signal": false}
  ],
  "notice": "Dados fictícios de demonstração, sem integração com sistemas reais da Getnet."
}
```

Para qualquer outra identidade a lista vem vazia, com `message` mandando informar a ausência sem estimar datas, valores ou diagnóstico. As consultas filtram por `workspace_id` e `user_id` (dois usuários e dois workspaces não se enxergam).

## Atendimento

| Método e rota | Resumo | Sucesso |
|---|---|---|
| `POST /chat` | Compatível com o desafio: envia a mensagem e espera a resposta | `200` ou `202` |
| `POST /conversations` | Cria conversa | `201` |
| `GET /conversations` | Lista as conversas do usuário (até 100, mais recentes primeiro) | `200` |
| `GET /conversations/{conversation_id}/messages` | Histórico ordenado (até 500) | `200` |
| `POST /conversations/{conversation_id}/runs` | Inicia execução assíncrona (cabeçalho `Idempotency-Key` obrigatório) | `202` |
| `GET /runs/{run_id}` | Estado autoritativo da execução | `200` |
| `GET /runs/{run_id}/events` | Eventos SSE; `?after=` ou `Last-Event-ID` | `200` |
| `GET /runs/{run_id}/evidence` | Trechos recuperados e citados | `200` |
| `POST /runs/{run_id}/cancel` | Cancela (idempotente) | `200` |
| `GET /health/live` | Liveness (não depende de banco nem de telemetria) | `200` |
| `GET /health/ready` | Readiness (faz `ping` no Mongo) | `200` ou `503` |

### `POST /chat`

Corpo: `message` (1 a 12000 caracteres) e `user_id` (obrigatórios) e `conversation_id` (opcional; omitido, cria uma conversa nova com o início da mensagem como título).

```bash
curl -s -X POST http://localhost:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "Minha maquininha está sem internet, o que eu faço?", "user_id": "cliente1988"}'
```

`200` quando a execução termina dentro de `CHAT_WAIT_SECONDS` (90 s), em qualquer estado que não seja ativo. Corpo observado (resposta e `trace_id` de uma execução real de 03/11/2026; texto abreviado com `…`):

```json
{
  "conversation_id": "31de626e17f04b32aa8fea0ed1340db5",
  "run_id": "720ee154bf184368b543c48cc18cadb6",
  "answer": "O diagnóstico recebido indica **ausência de sinal da rede móvel** na maquininha, com chip Algar. **Atenção: os dados do diagnóstico são fictícios, de demonstração.** … [1][6]",
  "sources": [
    {"evidence_id": 1, "kind": "official", "title": "Dúvidas frequentes (página oficial)", "url": "https://site.getnet.com.br/duvidas/", "retrieved_on": "2026-11-04"},
    {"evidence_id": 6, "kind": "official", "title": "Dúvidas frequentes (página oficial)", "url": "https://site.getnet.com.br/duvidas/", "retrieved_on": "2026-11-04"}
  ],
  "trace_id": "75a626118805a08bcbd5441bed1f8c31",
  "status": "completed"
}
```

`sources` traz só as fontes **citadas** com `[n]` válido na resposta; `kind` é `curated`, `official` ou `web`. Para ver tudo o que foi recuperado, use `GET /runs/{run_id}/evidence`. Resposta em outro estado terminal (`cancelled`, `interrupted`) também é `200`, com o texto parcial em `answer`.

`202` se a execução ainda está ativa após a espera (ela continua em segundo plano; formato ilustrado com os ids do exemplo anterior):

```json
{
  "conversation_id": "31de626e17f04b32aa8fea0ed1340db5",
  "run_id": "720ee154bf184368b543c48cc18cadb6",
  "trace_id": "75a626118805a08bcbd5441bed1f8c31",
  "answer": null,
  "sources": [],
  "status": "running",
  "events_url": "/api/v1/runs/720ee154bf184368b543c48cc18cadb6/events",
  "run_url": "/api/v1/runs/720ee154bf184368b543c48cc18cadb6"
}
```

Falha de execução responde `502 run_failed` com `details: {conversation_id, run_id, trace_id, error_code}` (por exemplo, `model_error`, `limit_timeout`); a mensagem é sempre sanitizada, sem detalhe do provedor.

### Conversas e execuções assíncronas

```bash
# criar conversa
curl -s -X POST http://localhost:8000/api/v1/conversations \
  -H 'Content-Type: application/json' -d '{"title": "Dúvidas sobre Pix"}'
# {"id":"…","workspace_id":"demo","user_id":"cliente1988","title":"Dúvidas sobre Pix","created_at":"…Z","updated_at":"…Z"}

# iniciar a execução (202); repetir a mesma chave com o mesmo corpo devolve o mesmo run
curl -s -X POST http://localhost:8000/api/v1/conversations/$CONV/runs \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: pix-001' \
  -d '{"message": "Do I need a bank account to receive my sales via Pix?", "user_id": "cliente1988"}'
# {"conversation_id":"…","run_id":"…","status":"queued","events_url":"/api/v1/runs/…/events"}

curl -s http://localhost:8000/api/v1/conversations/$CONV/messages
curl -s http://localhost:8000/api/v1/runs/$RUN
```

Uma conversa executa uma mensagem por vez: com outra chave enquanto a anterior roda, `409 conversation_busy`. O mesmo vale para o `conversation_id` informado em `/chat`.

`GET /runs/{run_id}` (campos de uma execução real concluída; `partial_text` abreviado):

```json
{
  "id": "720ee154bf184368b543c48cc18cadb6",
  "run_id": "720ee154bf184368b543c48cc18cadb6",
  "conversation_id": "31de626e17f04b32aa8fea0ed1340db5",
  "status": "completed",
  "final": true,
  "graph_version": "4",
  "trace_id": "75a626118805a08bcbd5441bed1f8c31",
  "partial_text": "O diagnóstico recebido indica … [1][6]",
  "sources": [{"evidence_id": 1, "kind": "official", "title": "Dúvidas frequentes (página oficial)", "url": "https://site.getnet.com.br/duvidas/", "retrieved_on": "2026-11-04"}],
  "usage": {"input_tokens": 14945, "output_tokens": 1767, "total_tokens": 16712, "cost_usd": null, "pricing_version": "2026-11-03.1"},
  "error": null,
  "cancel_requested": false,
  "seq": 38,
  "created_at": "2026-11-04T02:11:17.288000Z",
  "updated_at": "2026-11-04T02:11:42.012000Z",
  "events_url": "/api/v1/runs/720ee154bf184368b543c48cc18cadb6/events"
}
```

Estados: `queued`, `running`, `completed`, `failed`, `cancelled`, `interrupted`. `usage.cost_usd: null` significa "não informado".

`GET /runs/{run_id}/evidence`:

```json
{
  "run_id": "720ee154bf184368b543c48cc18cadb6",
  "status": "completed",
  "retrieved": [
    {"evidence_id": 1, "kind": "official", "chunk_id": "official-duvidas#3", "source_id": "official-duvidas", "title": "Dúvidas frequentes (página oficial)",
     "heading": "Algar", "url": "https://site.getnet.com.br/duvidas/", "version": "p1-c1200-o150:823888ba8d44", "retrieved_on": "2026-11-04",
     "limitations": ["FAQ extensa com conteúdo de diferentes épocas; confirmar a data e o produto antes de orientar."],
     "document_type": "official_page", "text": "Como insiro o Chip na minha máquina? …", "cited": true}
  ],
  "cited": [{"evidence_id": 1, "…": "itens de retrieved com cited: true"}],
  "invalid_citations": []
}
```

(`retrieved` tem 6 itens nessa execução e `cited` repete os dois citados; o exemplo abrevia ambas as listas.) `invalid_citations` lista números `[n]` do texto que não correspondem a nenhum trecho recuperado, para o cliente não exibi-los como fonte.

`POST /runs/{run_id}/cancel` devolve o mesmo corpo de `GET /runs/{run_id}` com o estado real; veja [eventos.md](eventos.md).

## Formato de erro

Toda rota `/api/v1` responde erros como JSON, antes de iniciar qualquer stream:

```json
{"code": "forbidden", "message": "Usuário não autorizado.", "request_id": "57c8208762704e79aefaf762ba98036c"}
```

`details` aparece só quando há informação útil. Erros de validação (`422 validation_error`) trazem `details: [{loc, message, type}]` e nunca devolvem o valor enviado:

```json
{"code": "validation_error", "message": "Requisição inválida.", "request_id": "5fd5b2664d6f4df1aa6aca2bd4d7eeae",
 "details": [{"loc": ["body", "message"], "message": "Field required", "type": "missing"}]}
```

O `request_id` é o mesmo do cabeçalho `X-Request-ID`; informe-o ao suporte. Falhas inesperadas viram `500 internal_error` sem detalhe interno.

| Status | `code` | Quando |
|---|---|---|
| 400 | `invalid_cursor` | Cursor SSE ou de paginação inválido; `invalid_if_match` (`If-Match` sem revisão numérica) |
| 401 | `unauthorized` | Só no perfil `production`: token Bearer ausente, inválido ou expirado (`WWW-Authenticate: Bearer`) |
| 403 | `forbidden` | `user_id` diferente do usuário autenticado, ou rota de administração sem papel `admin` |
| 404 | `conversation_not_found`, `run_not_found`, `agent_not_found`, `version_not_found`, `prompt_not_found`, `source_not_found`, `ingestion_not_found`, `not_found` | Recurso inexistente ou de outro usuário |
| 405 | `method_not_allowed` | Método inválido |
| 409 | `conversation_busy`, `idempotency_conflict`, `graph_invalid`, `graph_conflict`, `no_changes`, `id_conflict`, `no_sources` | Conflito de estado (veja cada rota) |
| 410 | `cursor_expired` | Eventos além da retenção; `details: {run_id, first_available_seq}` |
| 412 | `revision_conflict` | `If-Match` desatualizado; `details: {current_revision}` |
| 422 | `validation_error`, `secret_detected`, `unknown_source` | Corpo ou parâmetro inválido; texto de agente com cara de credencial |
| 428 | `precondition_required` | `PATCH /agents/{id}` sem `If-Match` |
| 429 | `capacity_exceeded` | Execuções ativas do workspace no limite (`MAX_ACTIVE_RUNS`); cabeçalho `Retry-After: 5` |
| 500 | `internal_error` | Falha inesperada |
| 502 | `run_failed` | `POST /chat` cuja execução falhou |
| 503 | `provider_not_configured`, `service_unavailable` | Sem `OPENAI_API_KEY`; banco indisponível em `/health/ready`; provedor de identidade indisponível (produção) |


## Administração de agentes

Rotas restritas a `admin`. Rascunhos usam `ETag`/`If-Match` com a `revision` inteira (`"2"` ou `2`); versões publicadas são imutáveis.

| Método e rota | Resumo |
|---|---|
| `GET /agents` | Lista (`q`, `kind`, `status=draft\|published`, `limit` 1–100, `cursor`); `{items, next_cursor}` |
| `POST /agents` | Cria rascunho (`name` e `instructions` obrigatórios); `201` com `ETag` |
| `GET /agents/{id}` | Detalhe com instruções, modelo, limites e versão publicada; `ETag` |
| `PATCH /agents/{id}` | Edita campos enviados; exige `If-Match` (428 sem ele, 412 se desatualizado) |
| `GET /agents/{id}/relationships?state=draft\|published` | Origens (`inbound`) e destinos (`outbound`) |
| `POST /agents/{id}/relationships/validate` | Valida o conjunto proposto contra o grafo publicado, sem salvar |
| `POST /agents/{id}/publish` | Publica atomicamente (`If-Match` opcional); `201` |
| `GET /agents/{id}/versions`, `GET /agents/{id}/versions/{version}` | Histórico e detalhe |
| `GET /agents/{id}/versions/{a}/diff/{b}` | Diferença campo a campo e por linha nas instruções |
| `POST /agents/{id}/versions/{version}/restore` | Restaura como **nova** versão; `201` |
| `GET /prompts/{prompt_id}/versions` | Versões do prompt |
| `GET /graph/versions` | Versões do grafo (agente → versão fixada) |

Ferramentas conhecidas para `tools`: `delegate_to_agent`, `search_knowledge`, `web_search`, `get_receivables`, `get_terminal_status`. Validar o ciclo (resposta real, grafo de 03/11/2026):

```bash
curl -s -X POST http://localhost:8000/api/v1/agents/knowledge/relationships/validate \
  -H 'Content-Type: application/json' \
  -d '{"delegates": ["router"], "tools": ["delegate_to_agent"]}'
```

```json
{
  "valid": false,
  "graph_revision": 4,
  "stale": false,
  "errors": [
    {"code": "cycle", "message": "Ciclo: knowledge → router → support → knowledge", "agent_id": "knowledge", "path": ["knowledge", "router", "support", "knowledge"]},
    {"code": "cycle", "message": "Ciclo: knowledge → router → knowledge", "agent_id": "knowledge", "path": ["knowledge", "router", "knowledge"]}
  ],
  "cycle": ["knowledge", "router", "support", "knowledge"]
}
```

Editar e publicar (o `ETag` vem de `GET`; `If-Match` desatualizado responde 412):

```bash
curl -s -X PATCH http://localhost:8000/api/v1/agents/$AGENT \
  -H 'Content-Type: application/json' -H 'If-Match: "2"' \
  -d '{"description": "Atende dúvidas de Pix", "limits": {"max_output_tokens": 500}}'

curl -s -X POST http://localhost:8000/api/v1/agents/$AGENT/publish \
  -H 'Content-Type: application/json' -H 'If-Match: "3"' \
  -d '{"summary": "Ajuste de instrução"}'
# {"agent": {...}, "version": 2, "prompt_version": 2, "graph_version": "5"}
```

Publicar um grafo com ciclo responde `409 graph_invalid` com `details.errors` e `details.cycle`; a versão anterior continua ativa. Sem mudanças desde a última publicação: `409 no_changes`. Publicações concorrentes que esgotam as três tentativas de revalidação: `409 graph_conflict`.

## Base de conhecimento

Rotas restritas a `admin`.

| Método e rota | Resumo |
|---|---|
| `GET /knowledge/sources` | Catálogo (20 fontes: 10 curadas e 10 oficiais) com status (`pending`, `indexed`, `stale`, `failed`), `chunk_count`, `index_version`, `retrieved_on`, `last_attempt` |
| `GET /knowledge/sources/{source_id}` | Detalhe com os trechos (sem vetores) |
| `POST /knowledge/ingestions` | Cria job (`202`); corpo opcional `{"source_ids": [...]}`; omitido, todas as fontes |
| `GET /knowledge/ingestions` e `/{job_id}` | Jobs recentes (`limit` 1–100) e estado de um job, com as cinco fases (`collect`, `extract`, `chunk`, `embed`, `index`) |

`POST /knowledge/ingestions` responde `409 no_sources` se o worker ainda não registrou o catálogo, e `422 unknown_source` com `details.source_ids` para ids inexistentes. O job é consumido pelo worker; acompanhe `status` (`queued`, `running`, `succeeded`, `partial`, `failed`) e `results` por fonte (`updated`, `already_current` ou `failed` com fase, código e mensagem). Exemplo de resultado de falha: uma fonte com `outcome: failed`, `phase: extract` e `code: empty` quando a página não traz texto útil no HTML; as demais fontes do job seguem e o job fica `partial`.

## Como os exemplos foram validados

Os exemplos de requisição (corpos JSON, cabeçalhos obrigatórios, parâmetros de consulta e combinação método e rota) foram conferidos contra o OpenAPI gerado pelo código:

```bash
cd backend
PYTHONPATH=src .venv/bin/python -c "import json; from app.adapters.inbound.http.main import app; print(json.dumps(app.openapi()))" > /tmp/openapi.json
```

O OpenAPI das rotas descreve os corpos de entrada, mas as respostas são dicionários sem esquema; por isso os corpos de resposta foram comparados com chamadas reais (execução de 03/11/2026 e consultas ao catálogo, agentes e histórico) e com os testes de contrato em `backend/tests/test_core_api.py`, `test_agents_api.py` e `test_core_errors.py`.
