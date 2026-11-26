# Proposal

## Why

Hoje o agente de escalonamento abre um protocolo e o caso para ali: o ticket fica `aberto` em `handoffs` e nenhuma pessoa o assume, responde ou encerra. A avaliação do desafio apontou exatamente esse ponto (Human Oversight 6,5): falta fechar o ciclo humano, com fila, atendente que assume, conversa com o cliente e retorno ao agente.

## What Changes

- Criar a área do atendente em `/atendente`, com login de atendentes fictícios (criados na carga de demonstração, senha só de teste) e papel `attendant`.
- Lista de protocolos com abas Abertos, Meus e Encerrados, mostrando protocolo, motivo, tempo de espera e quem assumiu.
- Detalhe do protocolo com o resumo escrito pelo agente, a conversa completa, o motivo, os dados de suporte do cliente (terminais e recebíveis), notas internas e as ações **Assumir conversa**, **Enviar mensagem** e **Encerrar atendimento** (com nota de resolução).
- Quando um protocolo é aberto, a conversa passa ao modo humano: mensagens do cliente deixam de iniciar execuções do agente e ficam para o atendente.
- Assumir é atômico: se dois atendentes clicam ao mesmo tempo, só o primeiro fica com o protocolo.
- O chat do cliente mostra as mensagens do atendente com o selo "Atendente <nome>" e um aviso de atendimento humano, buscando novas mensagens periodicamente enquanto a conversa está com uma pessoa.
- **Encerrar atendimento** fecha o protocolo, avisa o cliente e devolve a conversa ao Roteador; o histórico enviado ao Roteador passa a incluir as falas do atendente.
- Trilha de auditoria por protocolo (aberto, assumido, mensagens, notas, encerrado: quem e quando) e métricas no Grafana: protocolos abertos, tempo até assumir e tempo até encerrar.
- **BREAKING** (comportamento): em uma conversa com protocolo aberto, `POST /chat` e `POST /conversations/{id}/runs` não iniciam mais execução; respondem que a mensagem foi entregue ao atendimento humano.

## Capabilities

### New Capabilities

- `operator-access`: identidade e autenticação dos atendentes, papel `attendant` e o que esse papel pode ver e fazer.
- `human-handoff`: ciclo de vida do protocolo (aberto, em atendimento, encerrado), modo humano da conversa, mensagens entre atendente e cliente, retorno ao Roteador, auditoria e métricas.
- `operator-workspace`: telas da área do atendente (login, lista de protocolos, detalhe) e as mudanças no chat do cliente durante o atendimento humano.

### Modified Capabilities

Nenhuma em `openspec/specs/` (a mudança `sistema-multiagente-atendimento` ainda não foi arquivada). O comportamento novo do chat em modo humano está descrito em `human-handoff`.

## Impact

- **Backend:** novas rotas `/api/v1/operator/*` (login, protocolos, assumir, mensagens, notas, encerrar); `handoffs` ganha estados, atendente responsável, trilha de eventos e índice de protocolo aberto por conversa; `conversations` ganha o modo humano; `messages` ganha o papel `operator`; o histórico do Roteador passa a incluir essas mensagens; `begin_run` e `/chat` desviam mensagens em modo humano.
- **Identidade:** novo papel `attendant` (o `operator` já existente é o cliente em produção); no perfil `local-demo`, token de sessão de atendente assinado pelo servidor; no perfil `production`, o papel vem do token OIDC.
- **Frontend:** rotas `/atendente` e `/atendente/protocolos/:protocol`; o chat do cliente exibe mensagens do atendente e o estado do atendimento.
- **Dados de demonstração:** atendentes fictícios na carga inicial, com senha de teste registrada em `.env.example`.
- **Observabilidade:** métricas e painéis novos no dashboard provisionado.
- **Docs:** README (seções de fluxo, guardrails e API), `docs/api.md`, `docs/eventos.md`, `docs/arquitetura.md`.
