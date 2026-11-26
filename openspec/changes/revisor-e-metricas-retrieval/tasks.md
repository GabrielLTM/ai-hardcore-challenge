# Tasks

Testes do backend: `cd backend && .venv/bin/python -m pytest`. Testes do frontend: `cd frontend && npm test`. Nenhuma tarefa chama a OpenAI real; as rodadas reais ficam em "Workflow follow-up" e só rodam com autorização do Gabriel.

## 1. Avaliação de retrieval

- [x] 1.1 Adicionar `mode` (`hybrid`, `vector`, `text`) a `hybrid_search`, mantendo `hybrid` como padrão e a ferramenta `search_knowledge` sem o parâmetro; verificar com teste de cada modo no Mongo local e teste de que a ferramenta continua híbrida
- [x] 1.2 Implementar `recall_at`, `reciprocal_rank` e `ndcg_at` em `evaluation/metrics.py` com deduplicação por fonte na melhor posição; verificar com testes de casos conhecidos (relevante na posição 2, fonte repetida, nenhuma relevante, relevância graduada)
- [x] 1.3 Escrever `tests/evaluation/retrieval_v1.jsonl` (~30 consultas pt-BR/inglês com relevância graduada e seção opcional) e a validação contra o manifesto; verificar com teste que recusa fonte inexistente e aceita o dataset
- [x] 1.4 Criar `python -m evaluation.retrieval`: modos, cache de embeddings por (modelo, sha256), `--embed` com `--max-embedding-tokens` obrigatório, só textual quando o cache não cobre; verificar com testes de recusa sem orçamento, uso integral do cache sem chamar o provedor e queda para o modo textual
- [x] 1.5 Gerar o relatório JSON e Markdown (métricas por modo, por consulta, divergências, metadados); verificar com teste de renderização e rodando o modo textual contra a base local
- [x] 1.6 Documentar em `docs/avaliacao.md` e no README (o que é cada métrica, como rodar, o que os números provam e o que não provam); verificar que os comandos documentados rodam

## 2. Revisor no domínio e na porta do modelo

- [x] 2.1 Adicionar `AgentKind.REVIEWER`; `check_graph` recusa delegação ao Revisor e Revisor com ferramentas; verificar com testes de grafo
- [x] 2.2 Adicionar `response_schema` a `ModelGateway.stream`, enviar `text.format` `json_schema` estrito no gateway da OpenAI e aceitar o parâmetro no modelo falso; verificar com teste do corpo da requisição montado (sem rede) e do falso
- [x] 2.3 Mover o detector de afirmações numéricas para o domínio e reusá-lo em `evaluation/metrics.py` sem mudar a regra; verificar com os testes de métricas existentes passando
- [x] 2.4 Definir o esquema e a validação do veredito (`aprovar`/`revisar`, notas 0–2 nos quatro critérios, problemas); verificar com testes de veredito válido, enum inválido, nota fora da faixa e JSON quebrado
- [x] 2.5 Criar o agente `reviewer` na carga de demonstração (instruções em pt-BR, sem ferramentas, sem delegações) sem sobrescrever um Revisor existente; verificar com teste de seed idempotente

## 3. Revisão e replanejamento no orquestrador

- [x] 3.1 Chamar o Revisor depois do Roteador quando houver evidência ou ferramenta de dados, com `review.started`/`review.completed` e `skipped` nos demais casos; verificar com testes do modelo falso para resposta com evidência, saudação e grafo sem Revisor
- [x] 3.2 Implementar a nova rodada com `revisar`: mesma `state` e mesmo ledger, crítica como entrada interna, `response.reset` antes do novo texto, teto `max_revisions = 1`, segunda revisão só para registro; verificar com testes de revisão feita, segunda reprovação sem terceira rodada e citações estáveis entre rodadas
- [x] 3.3 Verificar o orçamento antes da rodada (tokens e tempo restantes) e registrar `skipped_budget`; verificar com teste de limite baixo
- [x] 3.4 Tratar falha do Revisor como aberta (erro, esquema inválido, tempo); verificar com testes de que a execução conclui com a resposta original e registra `failed`
- [x] 3.5 Gravar `review` no resumo da execução em `_settle` e salvar só a resposta final na conversa; verificar com teste de API (`GET` da execução e das mensagens) e de reconexão SSE com `Last-Event-ID` depois do `response.reset`
- [x] 3.6 Documentar os eventos novos e o campo `review` em `docs/eventos.md` e `docs/api.md`, e o fluxo em `docs/arquitetura.md` e na seção de orquestração do README (diagrama com a etapa de revisão); verificar contra os testes da 3.x

## 4. Métricas e painel

- [x] 4.1 Adicionar `getnet.review.verdicts`, `getnet.review.revisions` e `getnet.review.duration` em `telemetry.py`, chamados pelo orquestrador; verificar com teste de telemetria sem atributos de cliente
- [x] 4.2 Adicionar ao dashboard provisionado a taxa de revisão, os vereditos por rodada e o p95 da duração; verificar com o teste de painéis existente estendido e abrindo o Grafana local depois de execuções com o modelo falso
- [x] 4.3 Registrar as métricas na seção de observabilidade do README

## 5. Frontend

- [x] 5.1 Tratar `response.reset` em `runs.ts` (zerar o texto acumulado) e os eventos de revisão; verificar com teste da reconstrução de texto com reset no meio
- [x] 5.2 Mostrar "Revisando a resposta" no chat entre `review.started` e `review.completed` ou o fim da execução, seguindo `DESIGN.md`; verificar com teste de componente
- [x] 5.3 Exibir o tipo Revisor na tela de agentes (rótulo e ausência de ferramentas/delegações); verificar com teste de componente

## 6. Avaliação semântica pelo Revisor

- [x] 6.1 Incluir no `run_eval` as notas e vereditos por caso e os agregados (aprovação na primeira rodada, taxa de revisão, média por critério, revisões que melhoraram); verificar com teste do relatório
- [x] 6.2 Roteirizar o Revisor no executor simulado (veredito por caso, com ao menos um `revisar` que leva a nova rodada) sem alterar as expectativas do `dataset_v1`; verificar com `python -m evaluation.run_eval` simulado mantendo 17/17
- [x] 6.3 Escrever `tests/evaluation/review_v1.jsonl` (~12 casos: corretos e com defeitos rotulados) e o executor de calibração (recall de `revisar`, especificidade, matriz por defeito) com `--max-cases` e `--max-total-tokens` obrigatórios no modo real; verificar com testes de recusa sem orçamento e do cálculo com vereditos simulados
- [x] 6.4 Documentar a avaliação semântica e a calibração em `docs/avaliacao.md` (o que o juiz mede, viés de juiz do mesmo modelo, como ler); verificar que os comandos simulados documentados rodam

## 7. Verificação integrada

- [x] 7.1 Rodar a suíte completa do backend e do frontend e subir a stack com o modelo falso, conferindo pela interface uma resposta aprovada, uma revisada (com troca de texto) e uma dispensada, e os painéis no Grafana

## 8. Resposta retida, evidência web e curadoria

- [x] 8.1 Reter o texto da raiz quando há Revisor e evidência ou dados do cliente, transmitindo-o só depois do veredito (aprovada, refeita ou original), com `response.reset` só se algo saiu ao vivo; verificar com testes de que nenhum delta da resposta reprovada é emitido, de que a saudação segue ao vivo e da retomada SSE
- [x] 8.2 Mostrar "Verificando a resposta" no chat enquanto o texto está retido; verificar com teste de componente
- [x] 8.3 Guardar o texto devolvido pela busca web nas evidências web; verificar com teste de que o Revisor recebe o texto e de que os eventos não o trazem
- [x] 8.4 Gravar agentes e fontes por rodada e as buscas vazias; implementar a agregação pura e `GET /api/v1/curation/reviews` (só admin, por workspace); verificar com testes da agregação, do `403` e do escopo
- [x] 8.5 Criar a tela "Curadoria" (totais, critérios, agentes, consultas sem evidência, exemplos com link e sugestões); verificar com teste de componente e pela interface
- [x] 8.6 Atualizar `docs/eventos.md`, `docs/arquitetura.md`, `docs/api.md`, o README e `docs/novidades-versao-2.md`; verificar contra os testes da 8.x
- [x] 8.7 Rodar as suítes e conferir pela interface com o modelo falso: resposta aprovada sem texto antes do veredito, resposta refeita sem a reprovada aparecer, e a curadoria com os dados gerados

## Workflow follow-up

- Rodadas com a OpenAI real só com autorização do Gabriel, sempre com teto: `evaluation.retrieval --embed` (embeddings das ~30 consultas, custo desprezível), calibração `review_v1` e `run_eval --real` com o Revisor ligado.
- Publicar os relatórios gerados e citar os números no README.
- Commit e push ficam com o Gabriel.
- Arquivar com `/opsx:archive` depois da implementação.
