# Tasks

Testes do backend: `cd backend && .venv/bin/python -m pytest`. Testes do frontend: `cd frontend && npm test`. Nenhuma tarefa chama a OpenAI real.

## 1. Identidade do atendente

- [x] 1.1 Adicionar `OPERATOR_SESSION_SECRET` e `OPERATOR_DEMO_PASSWORD` a `Settings` e `.env.example` (gera segredo por processo com aviso no log quando vazio); verificar com teste de settings
- [x] 1.2 Criar coleção `operators` na carga de demonstração com Ana e Bruno, senha em `hashlib.scrypt` com sal; verificar com teste que o hash confere e a senha não é gravada em claro
- [x] 1.3 Implementar `POST /api/v1/operator/login` (só `local-demo`, por e-mail e senha) emitindo JWT HS256 de 8 h; verificar com testes de login válido, senha errada e e-mail inexistente com a mesma resposta `401`, e indisponível em `production`
- [x] 1.3a Implementar `GET /api/v1/operator/demo-accounts` (só `local-demo`) com nome, e-mail e senha de teste; verificar com teste de que devolve Ana e Bruno em `local-demo` e `404` em `production`
- [x] 1.4 Implementar `current_operator` (JWT em `local-demo`, OIDC com o papel `attendant` em `production`); verificar com testes de token expirado, adulterado, token de atendente recusado em rota de cliente e de administração, e cliente recusado em rota de atendente
- [x] 1.5 Documentar login e papel `attendant` em `docs/api.md` e na seção de identidade do README; verificar que os exemplos de `curl` rodam contra a stack local

## 2. Ciclo do protocolo

- [x] 2.1 Estender `handoffs`: `active`, `operator`, `claimed_at`, `closed_at`, `resolution`, `timeline`; índice único parcial `(workspace_id, conversation_id)` com `active: true`; `open` devolve o protocolo ativo da conversa; verificar com teste de segundo encaminhamento na mesma conversa
- [x] 2.2 Implementar assumir atômico (`find_one_and_update` com `status: 'aberto'`), com mensagem `system` ao cliente e evento na trilha; verificar com teste de duas assunções concorrentes (uma `200`, outra `409` com o nome)
- [x] 2.3 Implementar envio de mensagem do atendente (papel `operator`, `author_name`) só pelo responsável; verificar com testes de envio pelo responsável e `403` para outro atendente
- [x] 2.4 Implementar notas internas só na `timeline`; verificar com teste de que a nota não aparece em `GET /conversations/{id}/messages`
- [x] 2.5 Implementar encerrar com nota obrigatória (`422` sem nota, `409` em transição inválida), mensagem `system` ao cliente e remoção de `active`; verificar com testes de cada caso
- [x] 2.6 Implementar `GET /api/v1/operator/handoffs` (filtros aberto, meus, encerrado; abertos do mais antigo ao mais novo; paginação por cursor como as demais listas) e `GET /api/v1/operator/handoffs/{protocol}` (resumo, conversa, trilha, terminais e recebíveis do cliente do protocolo); verificar com testes de escopo por workspace (`404` em outro workspace)
- [x] 2.7 Documentar rotas, estados e códigos de erro em `docs/api.md` e os novos tipos de mensagem em `docs/eventos.md`; verificar contra os testes da 2.x

## 3. Conversa em modo humano

- [x] 3.1 Desviar em `begin_run` quando há protocolo ativo: grava a mensagem do cliente, evento na trilha, sem execução; `POST /chat` responde `200` com `answer: null` e `status: 'with_operator'`; `POST /conversations/{id}/runs` responde `202` sem `events_url`; verificar com testes de que nenhuma execução é criada e o modelo falso não é chamado
- [x] 3.2 Incluir `handoff` em `GET /conversations/{id}/messages`; verificar com teste nos estados aberto, em atendimento e encerrado
- [x] 3.3 Ajustar `conversation_history` para incluir mensagens `operator` como `assistant` com prefixo `[Atendente <nome>]` e ignorar `system`; verificar com teste de que, após encerrar, a próxima execução do Roteador recebe a fala do atendente
- [x] 3.4 Atualizar a seção de fluxo de mensagens e de guardrails do README e `docs/arquitetura.md` com o modo humano; verificar que o diagrama/descrição citam o desvio e o retorno

## 4. Métricas

- [x] 4.1 Adicionar `getnet.handoffs` (opened, claimed, closed), `getnet.handoff.wait` e `getnet.handoff.handle` em `telemetry.py`, chamados nas transições; verificar com teste de telemetria que os pontos são registrados sem atributos de cliente
- [x] 4.2 Adicionar ao dashboard provisionado os painéis abertos agora, tempo até assumir (p50/p95) e tempo até encerrar; verificar abrindo o Grafana local depois de um ciclo completo
- [x] 4.3 Registrar as métricas novas na seção de observabilidade do README

## 5. Área do atendente (frontend)

- [x] 5.1 Criar `/atendente` com login por e-mail e senha, botões "Usar Ana (demonstração)" e "Usar Bruno (demonstração)" abaixo do formulário que preenchem os campos (só quando `demo-accounts` responde), guarda de rota e retorno ao login quando a sessão vence; verificar com testes de componente do preenchimento, da ausência dos botões sem contas e do redirecionamento
- [x] 5.2 Criar a lista com abas Abertos, Meus e Encerrados, tempo de espera e quem assumiu, com polling de 3 s; verificar com teste de componente das abas e do estado vazio
- [x] 5.3 Criar o detalhe do protocolo: resumo, motivo, conversa com autores identificados, terminais e recebíveis, notas, trilha e botões Assumir conversa, Enviar mensagem e Encerrar atendimento (modal com nota obrigatória); modo somente leitura quando outro assumiu; verificar com testes de componente do conflito `409` e do encerramento sem nota
- [ ] 5.4 Seguir `DESIGN.md` e `UX-CONTRACT.md` (estados de carregando, vazio, erro, acessibilidade por teclado); verificar navegando pela área com teclado

## 6. Chat do cliente

- [x] 6.1 Exibir o aviso fixo ("Aguardando um atendente" / "Você está falando com <nome>", com protocolo) e o selo "Atendente <nome>" nas mensagens `operator`, e mensagens `system` como avisos; verificar com teste de componente
- [x] 6.2 Fazer polling de mensagens a cada 3 s enquanto `handoff` não for nulo e tratar o retorno `with_operator` ao enviar (sem abrir SSE); verificar com teste do hook e recarregando a página em modo humano

## 7. Verificação integrada

- [x] 7.1 Teste de integração com modelo falso do ciclo inteiro: pedido de humano → protocolo aberto → mensagem do cliente desviada → Ana assume (Bruno recebe 409) → troca de mensagens → nota interna → encerrar → nova mensagem do cliente inicia execução do Roteador com a fala da Ana no histórico
- [x] 7.2 Incluir o caso de ciclo humano no `dataset_v1` só se não alterar expectativas existentes (senão `dataset_v2`); verificar com `python -m evaluation.run_eval` simulado. Não incluído: o dataset é de uma mensagem por caso e o ciclo exige ações do atendente; fica coberto por `test_attendant` (7.1). A avaliação simulada continua 17/17.
- [x] 7.3 Rodar a suíte completa do backend e do frontend e subir a stack com `docker compose up -d --build`, conferindo o ciclo pela interface nos dois lados

## Workflow follow-up

- Commit e push ficam com o Gabriel.
- Arquivar com `/opsx:archive` depois da implementação.
