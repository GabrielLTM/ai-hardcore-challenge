# Tasks

As seções 1–7 priorizam um núcleo demonstrável. A marcação não garante conclusão em 60 minutos: medir tempo e comunicar o que faltar. As seções 8–11 completam o escopo solicitado, incluindo administração e escala. Todas as tarefas pertencem à mudança; o corte mínimo não substitui a entrega completa.

## 1. Fundação e infraestrutura local

- [x] 1.1 Criar módulos Python hexagonais, dependências fixadas, configuração e cliente de modelo por portas; verificar importações e testes de dependência do domínio.
- [x] 1.2 Criar Dockerfile e Compose com MongoDB Atlas Local compatível com Search, API, worker de ingestão com volumes e health checks; verificar subida e acesso local documentados.
- [x] 1.3 Criar configuração segura com perfil local explícito, variáveis de ambiente e autenticação de demonstração; verificar que credenciais não aparecem no repositório e produção sem identidade falha ao iniciar.

## 2. Domínio, identidade e dados de demonstração

- [x] 2.1 Implementar entidades, portas e persistência para workspace, identidade, conversas e dados de suporte; verificar isolamento com dois usuários e dois workspaces.
- [x] 2.2 Criar seeds idempotentes para router, knowledge, support, grafo inicial e cliente fictício; verificar reexecução e indicação de dados simulados.
- [x] 2.3 Implementar ferramentas de recebíveis e terminais vinculadas à identidade do backend; testar retorno real dos fixtures, ausência e tentativa de troca de cliente.

## 3. Ingestão e recuperação de conhecimento

- [x] 3.1 Implementar ingestão dos documentos curados do manifesto preservando hash e metadados, e testar reingestão sem duplicação.
- [x] 3.2 Implementar captura das URLs oficiais com allowlist, validação de redirecionamento/tamanho, extração, chunks e falhas; testar bloqueio de SSRF e preservação da versão válida.
- [x] 3.3 Integrar embeddings configuráveis, índices textual/vetorial e busca híbrida com filtro de escopo; testar que as duas buscas e a fusão retornam fontes rastreáveis.
- [x] 3.4 Implementar ferramenta de busca web atual e contrato de fontes e indisponibilidade; testar com provider simulado e documentar custo externo.
- [x] 3.5 Implementar evidências recuperadas/citadas e tratamento de insuficiência ou conflito; testar URLs, limites, citações inválidas e escopo de prazos Pix.

## 4. Orquestração e endpoints

- [x] 4.1 Implementar roteador e agentes especializados com instruções e tools específicas, snapshot do grafo e proteção de identidade; testar produtos, web, suporte e cooperação.
- [x] 4.2 Implementar validação de delegação, limites de tempo/chamadas/tokens e conteúdo não confiável; testar agente não vinculado, tentativa de ciclo e prompt injection.
- [x] 4.3 Implementar `POST /chat` compatível com o PDF, erros estruturados e consulta autorizada de execução; testar payload original, resposta JSON e timeout 202.
- [x] 4.4 Documentar configuração dos providers, mock de suporte e endpoints de atendimento; verificar exemplos do README e OpenAPI contra as rotas.

## 5. Histórico e streaming

- [x] 5.1 Persistir conversas, mensagens, execução e eventos sequenciais com idempotência; testar repetição de requisição e conflito de chave.
- [x] 5.2 Implementar criação de run, SSE com cursor, heartbeat, replay e estado consultável; testar reconexão, duplicatas, UTF-8/SSE fragmentado, cursor expirado e eventos terminais.
- [x] 5.3 Implementar cancelamento explícito, persistência de parcial, lease e marcação de execução interrompida após crash; testar corrida cancelamento/conclusão e reconexão em outra instância.
- [x] 5.4 Documentar esquema de eventos, retenção e recuperação, e validar exemplos contra testes de contrato.

## 6. Interface de atendimento, correlação e custo

- [x] 6.1 Criar base React/TypeScript com componentes compartilhados, `DESIGN.md` e `UX-CONTRACT.md` provisórios; verificar build, tipagem, acessibilidade e possibilidade de trocar tokens pelo design do usuário.
- [x] 6.2 Implementar chat, histórico, estados de streaming, cancelamento e reconexão; verificar em navegador desktop/celular, teclado e com rede interrompida.
- [x] 6.3 Implementar timeline de router/agentes/tools e painel de trechos recuperados versus fontes citadas; verificar ligação com eventos reais, dados mascarados e acessibilidade.
- [x] 6.4 Registrar `trace_id` (id de correlação por execução, em `POST /chat` e `GET /runs/{id}`), tokens e custo estimado com preço versionado (`PRICING_VERSION`), exibidos em "Detalhes técnicos"; custo desconhecido aparece como não informado, nunca zero (OpenTelemetry e Grafana removidos do escopo em 04/11/2026).
- [x] 6.5 Devolver `X-Request-ID` e erros `{code,message,request_id}` sem detalhe do provedor nem credenciais, e documentar como investigar uma execução por `trace_id`/`request_id`.

## 7. Demonstração e testes do núcleo

- [x] 7.1 Automatizar cenários principais do PDF com doubles, incluindo Getnet, clima, recebíveis, crediário, recusa e link; verificar roteamento, fontes e ausência de respostas inventadas.
- [x] 7.2 Preparar exemplos de execução e testes com API real opcionais e limites de custo; documentar como executar sem expor chave e registrar resultados observados.
- [x] 7.3 Documentar arquitetura, RAG, fluxo de mensagens, execução Docker, testes e roteiro de demonstração de até 15 minutos; executar comandos documentados do início ao fim.

## 8. Configuração e versionamento de agentes

- [x] 8.1 Implementar CRUD de rascunhos, snapshots imutáveis de prompt/agente/grafo, histórico, diff e restauração como nova publicação; testar integridade e versão fixada por execução.
- [x] 8.2 Implementar relações diretas de entrada/saída, endpoint de validação e publicação transacional com CAS; testar auto-vínculo, ciclo, destino ausente e publicações concorrentes.
- [x] 8.3 Implementar endpoints autenticados de listagem, edição, publicação e versões com OpenAPI; testar permissões e respostas 409/412.
- [x] 8.4 Implementar telas React de listagem, edição, vínculos, validação, publicação e comparação de versões; verificar estados, conflito, foco e uso por teclado em navegador.

## 9. Gestão da base e execução durável

- [x] 9.1 Implementar fila de ingestão em Mongo com lease, timeout, reprocessamento idempotente e retry limitado; testar morte do worker, reprocessamento e falha sem substituir versão válida.
- [x] 9.2 Implementar telas de fontes, chunks, estado e reprocessamento autorizado; testar vazio, progresso por fase, falha e atualização.
- [x] 9.3 Implementar quotas compartilhadas e concorrência por workspace em Mongo com reservas atômicas; testar duas instâncias e exaustão controlada.

## 10. Preparação para produção e escala

- [x] 10.1 Implementar adapter de identidade OIDC para produção, vínculo seguro de usuário/workspace, RBAC e TLS por infraestrutura; testar 401, 403, expiração e isolamento entre usuários.
- [x] 10.2 Automatizar índices, migrações aditivas, retenção configurável e backup/restore para ambiente compatível; executar recuperação documentada em ambiente de teste.
- [x] 10.3 Medir baseline de 1, 10 e 50 clientes concorrentes, latência, SSE, banco, uso de modelo e falha de instância; registrar configuração, resultados e limites sem tratar cenários como capacidade garantida.
- [x] 10.4 Registrar critérios e custo operacional para Redis, broker/fila gerenciada ou Kafka, baseados no benchmark; verificar que a recomendação distingue necessidade medida de hipótese.
- [x] 10.5 Criar dataset versionado de avaliação de agentes e RAG, e relatório de roteamento, grounding, erros, latência e custo; executar com doubles e rodada real opcional com orçamento definido.

## 11. Verificação de ponta a ponta

- [x] 11.1 Executar testes unitários, contratos, integração Mongo, tipagem, build e browser com os comandos publicados; registrar resultados e corrigir falhas.
- [ ] 11.2 Demonstrar ambiente limpo com Compose, ingestão de URLs, quatro fluxos representativos, configuração e relacionamento, histórico/streaming, evidências, andamento e detalhes técnicos; registrar limitações observadas.
- [ ] 11.3 Verificar que rollback, recuperação de backup, limites e reprodução da execução refletem o comportamento implementado; atualizar documentação e registrar itens que dependam do design futuro ou ambiente externo.
