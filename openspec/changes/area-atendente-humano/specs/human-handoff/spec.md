# Spec Delta

## Purpose

Fecha o ciclo do encaminhamento humano: do protocolo aberto pelo agente até o atendente assumir, conversar com o cliente e devolver a conversa ao Roteador, com auditoria e métricas.

## ADDED Requirements

### Requirement: Estados do protocolo
Um protocolo SHALL ter um dos estados `aberto`, `em_atendimento` ou `encerrado`, e só SHALL avançar nessa ordem. Cada protocolo SHALL guardar o motivo, o resumo do agente, a conversa de origem, o atendente responsável (quando houver) e os horários de abertura, de assunção e de encerramento.

#### Scenario: Transição inválida
- **WHEN** um atendente tenta encerrar um protocolo `aberto` que ninguém assumiu, ou assumir um protocolo `encerrado`
- **THEN** a resposta é `409` e o estado não muda

### Requirement: Um protocolo ativo por conversa
Uma conversa SHALL ter no máximo um protocolo não encerrado. Se o agente pedir um novo encaminhamento enquanto já existe um, o sistema SHALL devolver o protocolo existente.

#### Scenario: Segundo pedido de humano na mesma conversa
- **WHEN** o encaminhamento é pedido numa conversa que já tem protocolo `aberto` ou `em_atendimento`
- **THEN** o mesmo número de protocolo é devolvido e nenhum novo é criado

### Requirement: Modo humano da conversa
Enquanto a conversa tiver protocolo `aberto` ou `em_atendimento`, uma nova mensagem do cliente SHALL ser registrada na conversa sem iniciar execução do agente, e a resposta SHALL indicar que a mensagem foi entregue ao atendimento humano, com o protocolo e o estado.

#### Scenario: Mensagem do cliente aguardando atendente
- **WHEN** o cliente envia mensagem numa conversa com protocolo `aberto`
- **THEN** a mensagem aparece na conversa, nenhuma execução é criada e a resposta informa que um atendente vai responder

#### Scenario: Endpoint do desafio em modo humano
- **WHEN** `POST /chat` recebe mensagem de uma conversa em modo humano
- **THEN** a resposta é `200` com `answer` nulo, `status` indicando atendimento humano e o protocolo, sem custo de modelo

### Requirement: Assumir protocolo
Um atendente SHALL poder assumir um protocolo `aberto`. A operação SHALL ser atômica: com pedidos simultâneos, exatamente um vence e os demais recebem conflito com o nome de quem assumiu. O cliente SHALL ser avisado de que um atendente entrou na conversa.

#### Scenario: Dois atendentes ao mesmo tempo
- **WHEN** dois atendentes pedem para assumir o mesmo protocolo ao mesmo tempo
- **THEN** um recebe sucesso e o outro recebe `409` informando quem assumiu

#### Scenario: Aviso ao cliente
- **WHEN** um atendente assume o protocolo
- **THEN** a conversa do cliente recebe uma mensagem de sistema "Atendente <nome> entrou na conversa"

### Requirement: Mensagens do atendente
Só o atendente que assumiu SHALL poder enviar mensagens ao cliente. As mensagens SHALL entrar na conversa com papel de atendente e com o nome de exibição dele, e SHALL ser vistas pelo cliente na consulta das mensagens da conversa.

#### Scenario: Atendente responsável envia mensagem
- **WHEN** o atendente que assumiu envia uma mensagem
- **THEN** ela aparece na conversa do cliente identificada como do atendente, com o nome dele

#### Scenario: Outro atendente tenta enviar
- **WHEN** um atendente que não assumiu o protocolo tenta enviar mensagem
- **THEN** a resposta é `403` e nada é gravado na conversa

### Requirement: Notas internas
Atendentes SHALL poder registrar notas internas no protocolo. Notas SHALL ser visíveis só na área do atendente e nunca SHALL aparecer para o cliente nem no histórico enviado ao modelo.

#### Scenario: Nota não vaza para o cliente
- **WHEN** um atendente registra uma nota interna
- **THEN** a nota aparece no detalhe do protocolo e não aparece nas mensagens da conversa do cliente

### Requirement: Encerrar atendimento e devolver ao Roteador
O atendente responsável SHALL poder encerrar o atendimento informando uma nota de resolução obrigatória. Ao encerrar, o protocolo SHALL ir para `encerrado`, o cliente SHALL receber o aviso "Atendimento encerrado. Você voltou a falar com o assistente virtual." e a conversa SHALL sair do modo humano. A próxima mensagem do cliente SHALL iniciar uma execução do Roteador cujo histórico inclui as mensagens do atendente, identificadas como dele.

#### Scenario: Encerrar sem nota
- **WHEN** o atendente pede para encerrar sem nota de resolução
- **THEN** a resposta é `422` e o protocolo continua `em_atendimento`

#### Scenario: Retorno ao Roteador com contexto
- **WHEN** o cliente escreve depois do encerramento
- **THEN** uma execução do Roteador é iniciada e o histórico recebido inclui o que o atendente disse

### Requirement: Trilha de auditoria
Cada protocolo SHALL guardar, em ordem, os eventos de abertura, assunção, mensagem do atendente, nota interna e encerramento, com o atendente e o horário de cada um. A trilha SHALL ser somente acréscimo.

#### Scenario: Consultar a trilha
- **WHEN** um atendente abre o detalhe de um protocolo encerrado
- **THEN** vê quem abriu (o agente e a execução), quem assumiu, quantas mensagens trocou, as notas e quem encerrou, com horários

### Requirement: Métricas de atendimento humano
O sistema SHALL publicar a quantidade de protocolos por estado, o tempo de abertura até assumir e o tempo de assunção até encerrar, sem identificar cliente nem conteúdo das mensagens.

#### Scenario: Painel no Grafana
- **WHEN** um protocolo é assumido e depois encerrado
- **THEN** o dashboard provisionado mostra o tempo até assumir e o tempo até encerrar desse atendimento
