# Proposal

## Why

O desafio exige um atendimento multiagente funcional, rastreável e fundamentado em fontes da Getnet. Precisamos definir uma solução demonstrável localmente, com fronteiras para crescer sem introduzir infraestrutura operacional antes de existir uma necessidade medida.

## What Changes

- Criar API Python/FastAPI com arquitetura hexagonal, agentes router, knowledge e support, execução limitada e comunicação por ferramentas.
- Disponibilizar chat React com geração e eventos de execução em tempo real, inspeção das fontes recuperadas e histórico persistido.
- Permitir configurar agentes, instruções, ferramentas e vínculos de delegação; versionar e publicar configurações e prompts com histórico e verificação de relacionamentos.
- Implementar ingestão de URLs oficiais e da base curada existente, busca híbrida no MongoDB e respostas com fontes; disponibilizar busca web e duas ferramentas de suporte com dados de demonstração.
- Registrar por execução um id de correlação (`trace_id`), tokens e estimativa de custos. Decisão de escopo de 04/11/2026: OpenTelemetry e Grafana ficam fora, para manter o núcleo enxuto e explicável (evolução futura).
- Definir autenticação, isolamento e limites de operação; fornecer perfil local e trajetória explícita para produção multiusuário.
- Documentar em detalhe requisitos, fluxos e estados do frontend para que o usuário crie o design posteriormente, preservando a implementação futura do React.
- Entregar estratégia de testes, avaliação, Docker Compose, plano de capacidade e operação, README e demonstração do desafio.

## Capabilities

### New Capabilities

- `agent-orchestration`: roteamento, delegação, execução limitada, ferramentas e transparência do fluxo.
- `agent-configuration`: cadastro, relacionamentos, versionamento, publicação e auditoria dos agentes e prompts.
- `chat-streaming-history`: conversas, histórico, eventos de streaming, recuperação, cancelamento e isolamento.
- `knowledge-retrieval`: ingestão rastreável, busca híbrida, fontes, conteúdo recuperado e pesquisa web.
- `customer-support`: consulta autorizada de dados fictícios de recebíveis e terminais com tratamento de ausência.
- `application-observability`: correlação por `trace_id`/`request_id`, logs sem conteúdo sensível, tokens e custo estimado por execução.
- `access-and-scaling`: identidade, autorização, quotas, disponibilidade e capacidade para expansão multiusuário.
- `frontend-workspace`: especificação funcional do React para chat, inspeção, histórico e administração.

### Modified Capabilities

Nenhuma: não existem especificações principais anteriores.

## Impact

Novo backend, frontend React, contratos HTTP/SSE, testes e configuração Docker. A base existente em `data/knowledge/getnet` será reaproveitada sem presumir embeddings ou ingestão já concluídos. OpenAI e busca web dependem de credenciais e rede; serviços locais usam MongoDB Atlas Local apenas para demonstração. O design visual final será fornecido pelo usuário; não há implementação nesta proposta.
