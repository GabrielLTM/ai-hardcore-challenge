# Spec Delta

## Purpose

Oferecer conversas persistidas com respostas progressivas e visibilidade confiável das execuções.

## ADDED Requirements

### Requirement: Endpoint de atendimento
O sistema SHALL aceitar POST de atendimento com `message` e `user_id` e oferecer resposta JSON significativa, identificador da conversa e da execução.

#### Scenario: Pergunta válida
- **WHEN** um cliente autorizado envia mensagem e identificador de usuário válidos
- **THEN** recebe a resposta ou execução identificável associada à sua conversa

### Requirement: Streaming de resposta e execução
O sistema SHALL transmitir eventos ordenados de texto, roteamento, agentes, ferramentas, fontes e conclusão enquanto a execução ocorre.

#### Scenario: Execução com delegação
- **WHEN** um agente é roteado e o modelo gera resposta
- **THEN** o cliente recebe eventos que identificam a delegação e deltas de texto antes do evento terminal

### Requirement: Retomada sem duplicação
O sistema SHALL atribuir identificador monotônico por execução aos eventos persistidos e permitir reconexão a partir do último identificador recebido.

#### Scenario: Conexão interrompida
- **WHEN** o cliente reconecta com cursor válido
- **THEN** recebe somente eventos posteriores disponíveis na mesma execução, sem criar outra execução nem duplicar texto

### Requirement: Recuperação após indisponibilidade
O sistema SHALL expor estado terminal ou em andamento da execução e sinalizar quando eventos expiraram ou a geração foi interrompida, sem afirmar continuidade inexistente.

#### Scenario: Instância de geração falha
- **WHEN** a instância responsável falha antes de concluir
- **THEN** a execução é marcada como interrompida após expiração de seu lease e o conteúdo persistido permanece consultável

### Requirement: Cancelamento explícito
O sistema SHALL permitir cancelamento autorizado da execução e persistir o conteúdo parcial com estado cancelado.

#### Scenario: Usuário interrompe geração
- **WHEN** o usuário solicita cancelamento de uma execução ativa
- **THEN** o backend solicita interrupção ao provedor, interrompe novas etapas e registra o estado final verificável

### Requirement: Histórico e idempotência
O sistema SHALL persistir conversas e mensagens por usuário e evitar mensagens ou execuções duplicadas em repetição com a mesma chave de idempotência.

#### Scenario: Repetir envio após timeout
- **WHEN** o cliente repete uma solicitação com a mesma chave e corpo
- **THEN** o sistema devolve a execução original sem criar uma segunda mensagem

#### Scenario: Reutilizar chave com corpo diferente
- **WHEN** a mesma chave é usada com conteúdo diferente
- **THEN** o sistema rejeita o conflito sem iniciar outra execução
