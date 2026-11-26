# Design

## Context

Motivação em `proposal.md`. Estado atual relevante:

- `Orchestrator.run` executa o Roteador (`_agent('router', ..., root=True)`) e devolve o texto. O texto da raiz é transmitido em `response.delta` enquanto é gerado. Os limites (`ExecutionLimits`: profundidade, delegações, ferramentas, tempo, tokens de saída) valem para a execução inteira, por um `state` compartilhado.
- `EvidenceLedger` numera trechos e fontes web, e `resolve` separa citações válidas de inválidas. `_settle` em `execution.py` grava evidências e o resumo da execução.
- Os agentes vêm do snapshot publicado do grafo (`AgentKind`: router, knowledge, support, custom). A carga de demonstração cria router, support, knowledge e escalation.
- `ModelGateway.stream` não tem saída estruturada. O gateway da OpenAI usa a Responses API.
- `MongoKnowledgeRepository.hybrid_search` roda `$vectorSearch` e `$search` em paralelo e funde por `rrf_fuse(k=60)`.
- A avaliação (`evaluation/`) tem `dataset_v1` (17 casos, `cites_any` por fonte curada), executor simulado e rodada real com orçamento. Não há métrica de ranking. A base curada tem 10 documentos em `data/knowledge/getnet`.
- O frontend reconstrói o texto da execução somando `response.delta` em `features/chat/runs.ts`, inclusive na reconexão.

## Goals / Non-Goals

**Goals:**
- Um julgamento de qualidade explícito e auditável, reutilizado em tempo de execução e na avaliação.
- Replanejamento real (nova rodada com nova evidência), com custo previsível e limitado.
- Números de retrieval reprodutíveis e quase sem custo de provedor.

**Non-Goals:**
- Revisão por trecho durante o streaming e bloqueio de resposta pelo Revisor (ele não censura: pede uma nova rodada).
- Reranker por modelo, ajuste de `k` do RRF ou mudanças no chunking (a ablação informa, não muda a busca).
- Avaliação humana de relevância em larga escala. O dataset é rotulado à mão, pequeno e versionado.

## Decisions

**1. O Revisor é um agente do grafo, chamado pelo orquestrador e não por delegação.** Ganha o tipo `reviewer` em `AgentKind`, versionamento, instruções editáveis, modelo e limites como os demais. A carga de demonstração cria `reviewer`. O orquestrador procura no snapshot o agente com `kind == reviewer` e o chama depois do Roteador. Nenhum agente pode delegar a ele (`check_graph` recusa), e ele não recebe ferramentas. Alternativa descartada: o Roteador delegar ao Revisor como ferramenta. O modelo decidiria se revisa, e a revisão deixaria de ser garantia.

**2. Saída estruturada na porta do modelo.** `ModelGateway.stream` ganha `response_schema: dict | None`. O gateway da OpenAI envia `text.format` com `type: json_schema` e `strict: true`. O modelo falso devolve o texto roteirizado. O veredito é validado em código (enum e faixa das notas): JSON inválido ou fora do esquema vira falha da revisão. Alternativa descartada: pedir JSON só no prompt e fazer parse permissivo, que é frágil justamente no caso que importa.

**3. Gatilho por evidência.** O Revisor roda quando `evidence.retrieved` não está vazio ou quando houve chamada bem-sucedida a `get_receivables` ou `get_terminal_status`. Recusas, saudações e `request_human_handoff` passam direto e emitem `review.completed` com `verdict: 'skipped'`. Isso reduz custo e latência (gap 5 da devolutiva) sem deixar de cobrir as respostas que afirmam fatos.

**4. Entrada do Revisor.** O Revisor recebe:
- a pergunta;
- a resposta;
- as evidências do ledger (id, tipo, título, seção, texto limitado a 2000 caracteres), dentro de `wrap_untrusted`;
- as citações inválidas de `resolve`;
- as afirmações numéricas sem citação. Para isso o detector de `evaluation/metrics.number_claims` sobe para o domínio, sem mudar a regra.

Revisada em 25/11/2026: o Revisor também recebe as últimas 6 mensagens da conversa (até 800 caracteres cada) como contexto, nunca como evidência. Sem isso, ele mandava tirar da resposta o que o próprio cliente ou o atendente tinha dito antes.

**5. Replanejamento = nova chamada ao Roteador com a crítica.** Com `revisar`, o orquestrador chama `_agent('router', ...)` de novo, com o mesmo `state` (os limites continuam somando), o mesmo ledger (os números de citação continuam estáveis) e uma entrada extra: a resposta anterior e os problemas, marcados como revisão interna. O Roteador pode delegar e buscar de novo. Antes da rodada, o orquestrador verifica se o orçamento restante comporta ao menos o tamanho da primeira resposta e se faltam mais de 20% do tempo. Se não comportar, entrega a primeira resposta com `revision: 'skipped_budget'`. A segunda resposta é julgada só para registro. Teto fixo de uma revisão (`ExecutionLimits.max_revisions = 1`).

**6. Streaming: reter até o veredito.** Revisada em 25/11/2026: na primeira versão a resposta era transmitida e trocada depois, e o cliente chegava a ler texto reprovado. Agora, quando há Revisor e a execução já tem evidência ou dados do cliente, o orquestrador acumula o texto da raiz sem emitir `response.delta` e só o transmite depois do veredito (a aprovada, a refeita ou a original em caso de falha ou orçamento). Texto gerado antes de haver evidência (saudação, recusa) segue ao vivo. `response.reset` fica só para o caso raro de algum texto ter saído ao vivo antes de a evidência chegar. Custo: o tempo de um julgamento (cerca de 2 a 4 s) antes de o texto aparecer, nas respostas com evidência; o chat mostra o andamento ("Verificando a resposta") nesse intervalo.

**13. Texto da evidência web.** `add_web_sources` passa a guardar o texto devolvido pela busca web (o mesmo que o agente leu, limitado a 2000 caracteres) em cada fonte daquela busca. Antes o texto era vazio e o Revisor só via título e URL.

**14. Curadoria sem chamada ao modelo.** Cada rodada do Revisor passa a guardar os agentes que participaram e as fontes recuperadas; a execução guarda as buscas na base que voltaram vazias. `GET /api/v1/curation/reviews?days=N` (só admin) lê `runs.review` e as perguntas em `messages` e agrega numa função pura: totais, critérios com nota abaixo de 2, taxa de revisão por agente, consultas sem evidência, exemplos revisados e sugestões. As sugestões são regras fixas por critério (fundamentação aponta busca ou instrução de citar; cobertura e limitações apontam instruções; segurança aponta guardrails) e por consulta sem evidência (lacuna na base). A tela "Curadoria" no menu de administração mostra isso.

**7. Falha do Revisor é aberta (fail-open).** Erro do modelo, esquema inválido ou tempo esgotado viram `review.completed` com `verdict: 'failed'` e código. A resposta do Roteador segue. Uma revisão que derruba a execução trocaria um risco pequeno (resposta sem segunda opinião) por um pior (cliente sem resposta). O tempo do Revisor é limitado pelo que resta do `timeout_seconds`.

**8. Resultado no resumo da execução.** `_settle` grava `review: {rounds: [{round, verdict, scores, issues, cost_usd}], revision}` no run. Os eventos levam os mesmos campos, com `issues` truncados por `mask`. `run_eval` lê o resumo e os eventos, e não precisa de rota nova.

**9. Métricas.** Em `telemetry.py`:
- contador `getnet.review.verdicts{verdict, round}`;
- contador `getnet.review.revisions{outcome}` (`done`, `skipped_budget`, `not_needed`);
- histograma `getnet.review.duration`.

O custo entra no `usage.recorded` existente, com `agent_id: reviewer`. Os painéis novos são taxa de revisão, vereditos por rodada e p95 da duração.

**10. Modos de busca para ablação.** `hybrid_search` ganha `mode: 'hybrid' | 'vector' | 'text'` (padrão `hybrid`). O modo escolhe quais pipelines rodam; com um só pipeline, não há fusão. Só a avaliação usa `vector` e `text`. A ferramenta `search_knowledge` não expõe o parâmetro.

**11. Executor de retrieval separado do `run_eval`.** `python -m evaluation.retrieval`:
- usa o Mongo local com a base curada já ingerida;
- consulta o repositório direto, sem HTTP e sem agentes, para medir só a busca;
- tem métricas puras (`recall_at`, `reciprocal_rank`, `ndcg_at`) em `evaluation/metrics.py`, deduplicando por `source_id` na melhor posição.

Embeddings das consultas:
- só com `--embed` e `--max-embedding-tokens`;
- com cache em `evaluation/cache/query_embeddings.json`, chave `(modelo, sha256 da consulta)`, versionado no git para que a rodada seja reprodutível sem custo.

Alternativa descartada: medir retrieval por `run_eval`, porque o modelo escolhe a consulta e isso mistura qualidade do prompt com qualidade da busca.

**12. Datasets.**
- `tests/evaluation/retrieval_v1.jsonl`: cerca de 30 consultas, pt-BR e inglês, incluindo paráfrases e consultas com termo exato (nome de produto, operadora). É aí que o textual deve ganhar do vetorial. Campos: `id`, `query`, `lang`, `relevant: {source_id: 1|2}`, `section?`.
- `tests/evaluation/review_v1.jsonl`: cerca de 12 casos com `question`, `evidence` (trechos fixos da base curada), `answer`, `expect: 'aprovar'|'revisar'` e `defect`.

O dataset do Revisor não depende de busca: mede só o julgamento.

## Risks / Trade-offs

- [O cliente vê a resposta mudar] → indicador "Revisando a resposta" desde `review.started`; a troca acontece em segundos e só na minoria revisada.
- [Custo e latência a mais] → gatilho por evidência, teto de uma revisão, orçamento compartilhado e painel de duração e custo. O Revisor pode ser desligado no grafo.
- [Revisor muito rígido gera revisões inúteis] → calibração com `review_v1` (especificidade nas respostas corretas) e métrica de notas que melhoraram após a revisão.
- [Juiz e réu do mesmo modelo] → a calibração mede o viés com defeitos conhecidos. O modelo do Revisor é configurável por agente, para usar outro.
- [Dataset de retrieval pequeno e rotulado pelo autor] → versionado, com hash no relatório. As métricas valem como comparação entre modos, não como número absoluto de produção.
- [Base com 10 documentos deixa Recall@5 fácil] → relatar também Recall@1, MRR e acerto de seção, que discriminam melhor.
- [Cache de embeddings obsoleto ao trocar o modelo] → a chave inclui o modelo; um modelo novo exige `--embed`.

## Migration Plan

- A carga de demonstração cria o agente `reviewer` quando ele não existe. Grafos já publicados sem Revisor continuam funcionando sem revisão.
- `check_graph` passa a aceitar o tipo `reviewer` e a recusar delegação a ele.
- Clientes do SSE fora deste repositório precisam tratar `response.reset` (documentado em `docs/eventos.md`).
- Rollback: despublicar ou desativar o Revisor no grafo. O orquestrador sem Revisor se comporta como hoje.
