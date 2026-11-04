# Design

## Context

Ver `proposal.md`. O repositório é greenfield para aplicação: contém OpenSpec e dez resumos curados com manifesto em `data/knowledge/getnet`; não há API, frontend, índices nem embeddings. O enunciado pede URL global, outras fontes, router/knowledge/support, RAG, busca web, duas tools de suporte, endpoint, Docker e estratégia de teste. O usuário quer também React, transparência, gestão de agentes e prompts, histórico e trajetória de escala. O desenho visual chegará depois.

O limite do desafio é 60 minutos de tempo de tela para desenvolver a solução central. O escopo completo solicitado excede esse núcleo como estimativa de risco, não como garantia de duração; `tasks.md` separa demonstração mínima de expansão sem alegar que o restante está concluído.

## Goals / Non-Goals

**Goals:** permitir uma implementação hexagonal, observável, multiusuário por contrato e reproduzível em desenvolvimento; definir APIs, UX e prioridades sem exigir Kafka ou Redis antecipadamente.

**Non-Goals:** integração real aos sistemas privados da Getnet; provisionamento pago, descoberta de capacidade real sem testes, escolha de identidade visual ou promessa de alta disponibilidade local. A interface inicial deve ser funcional e acessível, com tokens substituíveis quando o usuário fornecer o design.

## Decisions

### 1. Fronteiras hexagonais e comunicação

Monólito modular Python/FastAPI, sem microsserviços iniciais. Dependências seguem `adapters → application → domain`; domínio não importa SDK, FastAPI, ou Mongo. Portas tipadas para modelos/stream, documentos e retrieval, web search, ferramentas de suporte, agentes/prompts, conversas/eventos, identidade, relógio, custos, limites. Entradas HTTP e CLI, e saídas Mongo, OpenAI e web são adapters. Casos de uso compõem regras de autorização, validação do grafo, publicação, ingestão, execução e leitura; pools e ciclo de vida ficam no composition root.

```text
backend/
  src/app/
    domain/{agents, conversations, knowledge, support}/
    application/{ports, use_cases, dto}/
    adapters/inbound/{http, cli}/
    adapters/outbound/{mongo, openai, web}/
    bootstrap/                 # wiring, configuração, lifespan
  tests/{unit, contract, integration, evaluation}/
frontend/
  src/{app, features, shared}/ # React + TypeScript
infra/mongo/
data/knowledge/getnet/        # base curada existente
```

Arquitetura hexagonal evita acoplamento do domínio aos provedores; mais módulos não significam serviços independentes. Configuração inicial: router, knowledge e support e ferramentas `delegate_to_agent`, `search_knowledge`, `web_search`, `get_receivables`, `get_terminal_status`. Suporte consulta apenas identidade injetada pela aplicação, nunca um ID escolhido pelo modelo. Agentes são funções do fluxo, não processos necessários; especialização inclui prompt, limites e conjunto de tools. O modelo pode escolher delegar, mas o servidor valida vínculos, permissões e limites antes da execução. Nenhum conteúdo de ferramenta ou fonte muda permissões. A resposta do conhecimento usa evidência recuperada; dados de depósito vêm da tool. Buscar clima e câmbio na web; indisponibilidade é explícita.

Modelo LLM configurável com default `gpt-6-luna`, e embedding configurável `text-embedding-3-small`; validar acesso, API e identificadores disponíveis na conta ao implementar. Responses API com SDK oficial, sem framework de agentes obrigatório. Contagem de chamadas, prazo e orçamento são por execução; limites de exemplo iniciais: profundidade 3, 8 delegações, 12 tools, timeout 120 s e teto de saída configurável; são salvaguardas ajustáveis, não garantia de qualidade.

### 2. Dados, versões e grafo

Coleções sugeridas: `workspaces`, `users`, `agents`, `agent_versions`, `prompt_versions`, `graph_versions`, `conversations`, `messages`, `runs`, `run_events`, `knowledge_sources`, `knowledge_chunks`, `ingestion_jobs`, `demo_customers`, `receivables`, `terminals`, `audit_events`. Todas as consultas privadas são condicionadas por workspace e identidade; índices de unicidade, cursores e TTL são explícitos. Prompt e agente versionados separadamente: agente publicado aponta para versão imutável de prompt, lista de tools e parâmetros; grafo publicado aponta para versões específicas, permitindo snapshot consistente da topologia inteira por run. Rascunhos mutáveis com `revision`/ETag; versões publicadas imutáveis; restaurar cria versão nova. Expor comparação e trilha actor/time/change; não armazenar segredos na configuração de agentes.

Publicação de grafo executa detecção de ciclos/auto-vínculos, verificação de destinos e permissões com controle de revisão global; transação Mongo em replica set e revisão condicional impedem duas publicações concorrentes criarem ciclo por write skew. Criar índices únicos para `(workspace, agent_id, version)`, `(workspace, conversation_id, idempotency_key)` e `(workspace, run_id, sequence)` e consultas com índice para histórico. Política inicial: agentes e grafo são compartilhados dentro do workspace; dados de conversas pertencem a usuário, salvo permissão administrativa definida no controle de acesso. Seeds são dados fictícios e visivelmente identificados.

### 3. Protocolo HTTP e streaming

Todas as rotas `/api/v1` retornam erros JSON `{code,message,request_id,details?}` antes de iniciar stream. Datas UTC RFC 3339; paginação por cursor opaco; IDs gerados no backend. O contrato mínimo será publicado em OpenAPI e validado em testes:

| Método e rota | Comportamento |
|---|---|
| `POST /chat` | Compatibilidade com o desafio: `{message,user_id}`; em modo local usuário deve corresponder à identidade demo. Espera término até limite configurado e retorna `{conversation_id,run_id,answer,sources,trace_id,status}`; timeout retorna 202 com IDs e estado consultável. |
| `POST /conversations` e `GET /conversations` | Cria/lista conversas autorizadas, paginação por cursor. |
| `GET /conversations/{id}/messages` | Lista histórico ordenado, fontes, estado e IDs. |
| `POST /conversations/{id}/runs` | `{message,user_id}` mais `Idempotency-Key`; retorna 202 `{conversation_id,run_id,status,events_url}`. Mesma chave/corpo retorna o mesmo run; corpo diferente retorna 409. |
| `GET /runs/{id}` | Estado autoritativo, versão snapshot, parcial/final, uso e erros sanitizados. |
| `GET /runs/{id}/events?after={seq}` | `text/event-stream`, eventos persistidos com sequência maior que cursor; autenticação e autorização iguais aos demais endpoints. Aceita `Last-Event-ID` quando presente; rejeita cursores inválidos. |
| `POST /runs/{id}/cancel` | Solicita cancelamento idempotente; devolve estado real, não presume interrupção imediata do provedor. |
| `GET /agents`, `POST /agents`, `GET/PATCH /agents/{id}` | Lista/cadastra/edita rascunhos; `If-Match` para conflitos. |
| `GET /agents/{id}/relationships?state=draft|published` | Origens e destinos por ID, nome, versão e estado. |
| `POST /agents/{id}/relationships/validate` | Valida conjunto proposto no grafo de revisão indicada, devolve `valid` e caminho de ciclo/erro. Não salva. |
| `POST /agents/{id}/publish`, `GET /agents/{id}/versions`, `GET /prompts/{id}/versions` | Publicação atômica, histórico, comparação de versões por consulta e restauração como nova versão; conflitos 409/412. |
| `POST /knowledge/ingestions`, `GET /knowledge/ingestions/{id}` | Criação e acompanhamento de trabalho de ingestão autorizado. |
| `GET /runs/{id}/evidence` | Trechos realmente recuperados, URL, título, versão, data, limitações e citações identificadas. |
| `GET /health/live`, `GET /health/ready` | Liveness e dependências necessárias para atender; liveness não depende de banco. |

SSE `id: <seq>`, `event: <type>`, `data: <JSON>` com `{run_id,seq,occurred_at,type,agent_id?,parent_event_id?,payload}`. Eventos: `run.started`, `route.selected`, `agent.started/completed/failed`, `tool.started/completed/failed`, `retrieval.completed`, `response.delta`, `usage.recorded`, `run.completed/failed/cancelled/interrupted`. Eventos expõem nome/versão e informações sanitizadas; não expõem raciocínio privado do modelo, credenciais ou argumentos sensíveis. Fluxo pode mostrar decisões e resultados efetivamente emitidos, não pensamentos internos.

Persistir eventos sequenciais antes da emissão, inclusive deltas em lotes pequenos, e snapshot parcial; replicar texto exatamente uma vez pelo seq. A conexão é assinante da execução e seu fechamento não cancela a operação; botão Parar chama cancelamento explícito. Cada instância lê os eventos no armazenamento compartilhado, por polling limitado inicialmente; heartbeats SSE evitam idle timeout e paginação de replay limita memória. Retenção padrão proposta de eventos: 7 dias configuráveis; se cursor estiver fora da retenção, devolver 410 antes de abrir SSE e cliente busca `GET /runs/{id}` e histórico. Race entre término e cancelamento resolve para primeiro estado terminal persistido, exibido pela API. Streams lentos usam buffer limitado e desconectam o assinante para retomada, sem acumular memória sem limite.

A execução interativa roda na instância que a iniciou, com lease/heartbeat persistido; uma instância com tarefa de manutenção marca runs expirados como `interrupted`, mantendo parcial e eventos. Não prometer retomada de geração após crash nem refazer automaticamente chamada ao modelo: usuário inicia nova execução com nova chave, evitando cobrança e ações duplicadas. Reconnect enquanto instância estiver saudável funciona em outra réplica via banco. Cancelamento por outra instância persiste solicitação que o worker ativo verifica; chamada HTTP/provedor é cancelada quando suportado, sem alegar estorno ou cancelamento garantido. Evitar eventos fora de ordem com seq monotônico transacional e teste de concorrência.

### 4. Ingestão, RAG e evidências

Separar três origens: as dez sínteses curadas já existentes (derivadas, identificadas pelo manifesto), páginas oficiais capturadas automaticamente, e busca web temporária. Fontes oficiais têm allowlist de domínios e URLs explícitas; bloquear destinos privados, redirecionamentos fora da lista, SSRF, arquivos excessivos e tipos não suportados. Observação: texto da página e campos dinâmicos podem faltar; registrar falha, data e versão, e nunca sobrescrever último documento válido com página incompleta. Chunking por título/seção com limites e sobreposição configuráveis; persistir URL canônica, hash, fonte, `retrieved_on`, língua/região, permissões, index version e versão do embedding. Deduplicação por URL/hash/processador; reindexar quando conteúdo ou modelo mudar; não misturar embeddings incompatíveis. Separar fonte curada de fonte bruta, e rotular no output.

Consultar Mongo Search e Vector Search, aplicar filtro de tenant/visibilidade antes da busca e combinar listas via reciprocal rank fusion com top-k e orçamento de contexto configuráveis. Não tratar similaridade como probabilidade de verdade. O contexto ao modelo contém IDs de evidência; validar citações contra esses IDs e persistir quais trechos foram recuperados e quais foram citados. Se evidência insuficiente ou conflitante, declarar isso. A observabilidade nunca copia conteúdo integral por padrão. Atualização baseada em comando/ação explícita; ingestão é trabalho durável em Mongo com lease, retry limitado e idempotência, que pode executar em worker separado a partir da mesma imagem; localmente um worker no Compose. Isto justifica desacoplar ingestões longas sem um broker adicional. Busca web hospedada é ferramenta específica, com indicação de custo por consulta e fontes; não fabricá-las se indisponíveis.

### 5. Frontend React: especificação para design

**Usuários e linguagem:** operador de atendimento e administrador técnico, inicialmente a mesma pessoa; idioma pt-BR, horário `America/Sao_Paulo` configurável com data UTC preservada, valores monetários BRL quando aplicável. Layout profissional centrado em atendimento e transparência; identidade visual, cores, logotipo e tipografia aguardam o material do usuário. Evitar vender o mock de suporte como dado real.

**Navegação:** `Conversas`, `Agentes`, `Base de conhecimento`. Nome da aplicação e identidade visual por definir. Layout largo: coluna de conversas, área principal de mensagens e painel de execução/evidências recolhível; estreito: histórico e evidências em painéis acessíveis, sem bloquear o chat. URLs de conversas/recursos permitem navegação e retorno; nunca colocar segredos ou conteúdo sensível em query string.

**Tela Conversas:** lista paginada com busca, data, título e vazio; botão Nova conversa. Área de mensagens em ordem e indicador de origem do suporte fictício. Composer multilinha com Enviar, Enter envia e Shift+Enter insere linha, composição IME respeitada. Uma execução ativa por conversa; outras conversas permanecem navegáveis. Enquanto executa, exibir texto parcial, indicador de etapa e Parar; ao parar, manter parcial rotulado, preservar input e oferecer novo envio seguro. Estado de resposta: aguardando, roteando, executando, gerando, concluída, falhou, cancelada, interrompida e reconectando. Scroll acompanha apenas quando usuário está no fim; oferece “Ir para o fim” quando afastado. Evitar anunciar cada token a leitores de tela: status de etapa e blocos concluídos em região `aria-live` moderada. Markdown sanitizado, links externos seguros, estado de erro com `request_id` e retry que usa a mesma chave somente para a mesma intenção.

**Painel Execução:** timeline hierárquica ordenada: router → especialista → tool/recuperação → resposta; nome e versão do agente, horário, duração, status, sumário seguro, `trace_id` (em "Detalhes técnicos"), tokens e estimativa de custo quando disponível. Raciocínio interno não é apresentado. Estados: não iniciado, em andamento, sucesso, parcial, erro, cancelado; sem eventos, cursor expirado e execução interrompida. Expandir tool mostra entrada/saída autorizada e mascarada; respeitar políticas do backend. Mostrar claramente quando valor não foi informado, sem substituir por zero.

**Painel Fontes:** listas separadas de trechos recuperados e fontes citadas, tipo (curada, fonte oficial, web), título, trecho, URL, coleta, versão, idioma/região e restrições. A diferença entre recuperado e citado é explícita; resultado não usado não deve parecer citação. Clique abre documento original; ausência, conflito e fonte indisponível têm explicação. Resultados não são editáveis a partir do chat.

**Tela Agentes:** tabela com nome, descrição, tipo, status rascunho/publicado, versão, ferramentas e destinos, busca e ação Criar agente. Filtros, estado vazio, erro, paginação e ação de abrir detalhes. Identificadores gerados pelo servidor, únicos no workspace; o usuário pode definir nome/descrição sem quebrar vínculos. Rotas restritas a administrador.

**Detalhe/Edição:** campos nome, descrição, tipo de papel, instruções de prompt, modelo, limites, conjunto de tools e seleção múltipla de agentes delegáveis com nomes e descrições. Mostrar grafo de vínculos diretos de entrada/saída; alternativa textual à representação gráfica. Rascunho e publicado lado a lado com indicação da revisão e mudanças não salvas. Ações Salvar rascunho, Validar relações e Publicar; backend sempre revalida. Validação mostra caminho do ciclo; conflito apresenta revisão atual e opções recarregar, comparar ou copiar edição. Confirmar publicação quando altera permissões ou vínculos; não perder texto ao falhar. Auditoria de quem publicou e quando.

**Versões:** lista de versões de agentes, prompts e snapshots com autor, data, status e resumo; detalhe e diff legível; restaurar cria nova versão com confirmação; execução histórica sempre exibe versão usada. Sem edição ou exclusão silenciosa de versões publicadas.

**Base de conhecimento:** lista de fontes e seu tipo, URL, data, status e última indexação; detalhe de chunks, limitações e versão. Ação de ingestão/reprocessamento autorizada mostra fases reais (coletar, extrair, dividir, embeddings, indexar), sem percentual fictício. Falhas incluem etapa e ação de retry; não apaga índice válido. Mensagem específica para fonte vazia, inacessível ou já atualizada.

**Componentes e contratos:** shell, navegação, lista paginada, busca com limpar e cancelamento de requisição, formulários com rótulos/erros e foco na primeira falha, editor multilinha, seletor múltiplo, chips com texto e ícone, timeline, drawer/painel, diálogo acessível com foco restaurado, feedback inline e toast não exclusivo. Não usar diálogos nativos. Respeitar WCAG 2.2 AA, contraste, foco visível, ordem semântica, movimento reduzido e 200% zoom; sem depender de cor. Tratar loading, vazio, sem resultado, falha, 401, 403, timeout, 409/412, offline e autorização revogada. Componentes compartilham tokens e comportamentos. No apply, criar `DESIGN.md` e `UX-CONTRACT.md` a partir de evidências e do design recebido, sem inventar identidade visual final.

**Fluxos para o designer:** (1) abrir conversa → enviar → acompanhar timeline → expandir fontes → interromper/reconectar; (2) agente → rascunho → selecionar tools e vínculos → validar ciclo → comparar → publicar → ver histórico; (3) base → fonte → ingestão → progresso → sucesso/erro → conferir conteúdo. Incluir cópia realista em pt-BR, estados para desktop e celular, telas com texto extenso e contexto de privacidade. O desenho precisa contemplar as três áreas de chat, mas não impõe colunas fixas.

### 6. Infraestrutura, escala e justificativas

| Componente | Inicial local | Necessidade / gatilho para expandir |
|---|---|---|
| API Python | 1 container, worker de ingestão da mesma imagem | Réplicas horizontais e balanceador quando testes de carga mostrarem saturação ou necessidade de disponibilidade. Estado e eventos ficam no banco compartilhado. |
| MongoDB Atlas Local + Search | Um nó, volumes, índices e readiness | Somente desenvolvimento/CI; produção exige deployment Mongo compatível com Search/Vector Search, replica set, backups testados, TLS e capacidade de escala. Avaliar Atlas gerenciado ou self-managed conforme custo e operação. |
| Redis/cache | Não provisionar no início | Adicionar apenas após medir gargalo de leitura, necessidade real de limitação distribuída ou pub/sub de alto volume. TTL, invalidação de versão, chaves por tenant e fallback são obrigatórios. Rate limit local não é limite global: em múltiplas réplicas aplicar quota atômica no Mongo inicialmente; Redis se benchmark justificar. |
| Fila / Kafka | Sem Kafka; jobs de ingestão em coleção Mongo com lease | Broker se volume e workers exigirem backpressure, throughput/replay, múltiplos consumidores ou falhas além do que o banco comporta. Comparar fila gerenciada simples antes de Kafka; Kafka se retenção/replay e múltiplos consumidores justificarem custo operacional. Uso de fila não garante exatamente uma vez: idempotência/outbox permanecem necessários. |
| SSE/eventos | Gravar no Mongo e polling limitado | Medir custo de leitura, latência e conexões; considerar change streams/broker distribuído para fan-out após benchmark. Replay continua sustentado por registro durável. |
| OpenAI e web | Serviços remotos com limites configurados | Rate limiting, concorrência e reserva por workspace/modelo, backoff limitado e orçamento. Custos dependem do uso real e da política do provedor. |

Capacidade não está dimensionada: medir p50/p95/p99, erro, primeira resposta, concorrência SSE, taxa de consultas, lag de ingestão, throughput de eventos, RAM/CPU, tokens/custo e limites do provedor. Estabelecer baseline 1/10/50 clientes concorrentes e mix de rotas; os números são cenários de teste, não capacidade garantida ou gatilhos de provisionamento. Fazer teste de soak e falha de instância; definir metas com evidência antes de comprar serviços. Proteger backups, índices, retenção, migração e políticas de custo. Rede externa ao provedor ainda é necessária; local não significa offline.

### 7. Segurança, avaliação e operação

Perfil `local-demo` com identidade e workspace explicitamente configurados, sem credenciais privadas em código; ferramenta de suporte marcada como simulada. Perfil `production` exige provedor OIDC configurado, autenticação no servidor e associação verificada entre sujeito e workspace/cliente; nunca confiar no `user_id` livre. RBAC operador/admin aplicado no servidor, limites e reserva de orçamento atômicos e isolamento de documentos privados. Idempotency keys, PII minimizada, segredos por ambiente/secret manager, TLS, observabilidade sem payload sensível por padrão. Políticas de retenção e exclusão precisam ser configuráveis; sem número inventado para conformidade.

Testes: domínio e validação de grafo, CAS e idempotência, contratos HTTP/SSE e eventos fragmentados, autorização/isolamento, adapters com doubles e falhas, ingestão e dedup/reindex com Mongo local, recuperação híbrida e citações verificáveis, replay/cancelamento/crash/reconexão multi-instância, e UI browser/teclado/viewport. Usar os dez cenários do PDF e exemplos negativos: usuário não encontrado, ausência de evidência, conflito entre fontes, injection e ferramenta indisponível. Medir taxa de roteamento, grounding, evidências corretas, dados inventados, latência e custo com dataset versionado; separar teste determinístico de rodada opcional com OpenAI e limites de custo. Sem alegar que uma pontuação de similaridade garante verdade.

### 8. Decisão de escopo: observabilidade (04/11/2026)

OpenTelemetry e Grafana LGTM saem do escopo para manter o núcleo enxuto e explicável. Permanece: `trace_id` como id de correlação (uuid) por execução, em `POST /chat` e `GET /runs/{id}`; `X-Request-ID` e erros `{code,message,request_id}`; tokens e custo estimado por execução (`PRICING_VERSION`) em "Detalhes técnicos"; avaliação e benchmark. Evolução futura: OpenTelemetry + Grafana.

## Risks / Trade-offs

- [Demanda maior que 60 minutos] → primeiro entregar o corte mínimo em `tasks.md`, preservando rastreabilidade e expondo o que faltar; não omitir funcionalidades na apresentação final.
- [Modelo econômico erra roteamento/citações] → avaliação de cenários e modelo configurável; não prometer qualidade sem teste real.
- [Eventos gravados frequentemente sobrecarregam Mongo] → lotes pequenos e medição; manter replay e adotar broker apenas com evidência.
- [Resposta parcial em crash] → lease, estado `interrupted`, persistência incremental e nova execução explícita; não alegar recuperação automática da geração.
- [Fontes públicas dinâmicas, desatualizadas ou conflitantes] → limites de coleta, data/proveniência, alertas e revisão; snapshots curados não substituem pipeline de URLs.
- [API e worker distribuídos sem limites globais] → quotas atômicas compartilhadas e testes de concorrência; cache/processo local não é autoridade.
- [Visibilidade revela dados] → escopo por identidade, mascaramento, logs mínimos e logs sem conteúdo de mensagens.
- [Prompt/agent alterado durante execução] → snapshot de grafo e versões imutáveis.
- [Design visual ainda não fornecido] → implementação funcional com componentes acessíveis e tokens substituíveis; aplicar identidade depois sem assumir aprovação visual.

## Migration Plan

1. Provisionar Mongo e LGTM locais com versões fixadas, volumes, health checks e autenticação de demonstração explícita; verificar compatibilidade da arquitetura da máquina.
2. Aplicar índices e seeds idempotentes, registrar versões iniciais do grafo e clientes fictícios, e ingerir documentos curados e URLs oficiais.
3. Subir API/worker/frontend e verificar fluxo, segurança e custo com dados de teste; habilitar execução real somente com credencial configurada.
4. Para produção, implantar banco adequado, secrets, identidade e backups; preparar índices antes de migrar dados e alternar tráfego. Executar teste de recuperação e monitoramento antes de admitir usuários.
5. Rollback da aplicação por imagem anterior; esquema aditivo e versões imutáveis permitem reversão do código. Se migração de dados não for compatível, executar plano de restauração de backup testado; não recriar volumes nem apagar dados como rollback padrão.
