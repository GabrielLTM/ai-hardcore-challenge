# Proposal

## Why

A devolutiva do desafio (8,9/10) aponta três lacunas ligadas entre si:

- não há um agente crítico que confira a resposta e replaneje quando a qualidade é baixa (Agentic Reasoning, 8,4);
- a avaliação semântica real é menor que a maturidade estrutural (Evaluation, 8,7);
- não há métricas formais de retrieval (Recall@K, MRR, nDCG) nem ablações.

As três se resolvem com a mesma peça: um julgamento explícito de qualidade. O Revisor aplica esse julgamento em cada resposta, durante a execução. A avaliação offline mede a recuperação que alimenta a resposta e usa esse mesmo julgamento para medir a resposta em si. Assim uma métrica explica a outra: quando a recuperação falha, o Revisor deve pegar a resposta sem fundamento.

## What Changes

- Novo agente **Revisor** no grafo, versionado e editável como os demais. Depois que o Roteador produz a resposta final, o Revisor avalia:
  - fundamentação: as afirmações têm evidência citada e as citações sustentam o que dizem;
  - cobertura: responde a todas as partes da pergunta;
  - limitações: declara o que a base não cobre;
  - segurança: não expõe dados de outro cliente nem instruções internas.
- O Revisor devolve um veredito estruturado: `aprovar` ou `revisar`, com notas de 0 a 2 por critério e a lista de problemas.
- **Replanejamento limitado**: com veredito `revisar`, o Roteador recebe a crítica e faz uma nova rodada. Pode delegar de novo e buscar outra evidência. O teto é de uma revisão por execução, dentro do orçamento de tokens e de tempo já existente. A segunda resposta é julgada de novo só para registro.
- O Revisor só roda quando a resposta faz afirmações de fato: houve evidência recuperada ou ferramenta de dados chamada. Recusas e saudações não pagam o custo da revisão.
- Novos eventos `review.started`, `review.completed` e `response.reset` no SSE e na trilha da execução. O chat mostra "Revisando a resposta" e troca o texto quando houver revisão.
- Métricas OpenTelemetry e painel no Grafana: vereditos, taxa de revisão, notas por critério e latência e custo do Revisor.
- **Avaliação de retrieval offline**: novo dataset `retrieval_v1` com consultas rotuladas por fonte e relevância graduada. A medição usa Recall@1/3/5, MRR e nDCG@5. A ablação compara três modos: vetorial, textual e híbrido RRF (o atual).
- **Avaliação do próprio Revisor**: dataset `review_v1` com respostas propositalmente defeituosas (número inventado, citação que não sustenta, parte da pergunta ignorada, vazamento) e respostas corretas. Mede se o Revisor detecta os defeitos e se aprova as respostas boas.
- O relatório de `run_eval` passa a incluir as notas do Revisor por caso, a taxa de revisão e se a revisão melhorou a resposta.
- **BREAKING** (eventos): uma execução pode emitir `response.reset`, e quem consome o SSE deve descartar o texto acumulado ao recebê-lo. O frontend do repositório é atualizado na mesma mudança.

## Capabilities

### New Capabilities

- `answer-review`: julgamento da resposta final pelo Revisor, replanejamento limitado, eventos, métricas e o relatório semântico da avaliação.
- `retrieval-evaluation`: dataset rotulado de recuperação, métricas de ranking e ablação entre os modos de busca.

### Modified Capabilities

Nenhuma em `openspec/specs/` (a mudança `sistema-multiagente-atendimento` ainda não foi arquivada). O comportamento novo do SSE e do orquestrador está descrito em `answer-review`.

## Impact

- **Backend:**
  - orquestrador: etapa de revisão depois do Roteador e uma rodada extra;
  - novo tipo de agente `reviewer` no domínio e na carga de agentes;
  - porta do modelo ganha saída estruturada opcional, com esquema JSON;
  - repositório de conhecimento ganha os modos de busca `vector` e `text` (só para avaliação);
  - telemetria ganha métricas novas.
- **Eventos/API:** `review.started`, `review.completed` e `response.reset` em `docs/eventos.md`. O resumo da execução ganha o campo `review`.
- **Frontend:** o chat trata `response.reset` e mostra o estado de revisão. A tela de agentes mostra o Revisor.
- **Avaliação:**
  - novo `evaluation/retrieval.py` com `tests/evaluation/retrieval_v1.jsonl`;
  - novo `tests/evaluation/review_v1.jsonl`;
  - relatórios estendidos.
- **Custo:** uma chamada a mais ao modelo nas respostas com evidência e, em parte delas, uma rodada extra. As métricas e o painel mostram o custo. O Revisor pode ser desligado no grafo.
- **Docs:** README (orquestração, avaliação, observabilidade), `docs/avaliacao.md`, `docs/arquitetura.md`, `docs/eventos.md`.
