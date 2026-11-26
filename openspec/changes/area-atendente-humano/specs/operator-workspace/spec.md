# Spec Delta

## Purpose

Define as telas da área do atendente e como o chat do cliente se comporta durante o atendimento humano, para que os dois lados vejam o mesmo estado do protocolo.

## ADDED Requirements

### Requirement: Tela de login do atendente
A rota `/atendente` SHALL mostrar o login quando não houver sessão válida e SHALL levar à lista de protocolos depois do login. Sessão vencida SHALL voltar ao login com aviso. O frontend SHALL indicar que as credenciais são de demonstração.

#### Scenario: Preencher com conta de demonstração
- **WHEN** o perfil é `local-demo` e o atendente clica em "Usar Ana (demonstração)" abaixo do formulário
- **THEN** os campos de e-mail e senha são preenchidos com a conta da Ana, prontos para entrar; há um botão igual para o Bruno

#### Scenario: Sem contas de demonstração
- **WHEN** o perfil não é `local-demo`
- **THEN** os botões de preenchimento não aparecem

#### Scenario: Acesso sem sessão
- **WHEN** alguém abre `/atendente/protocolos` sem sessão
- **THEN** é levado ao login e, após entrar, volta à lista

### Requirement: Lista de protocolos
A lista SHALL ter as abas Abertos, Meus (em atendimento por mim) e Encerrados. Cada linha SHALL mostrar protocolo, motivo, tempo de espera (ou duração, quando encerrado) e quem assumiu. Abertos SHALL vir do mais antigo para o mais novo. A lista SHALL se atualizar sozinha periodicamente.

#### Scenario: Novo protocolo aparece
- **WHEN** o agente abre um protocolo enquanto a aba Abertos está na tela
- **THEN** o protocolo aparece na lista sem recarregar a página

### Requirement: Detalhe do protocolo
O detalhe SHALL mostrar o resumo escrito pelo agente, o motivo, a conversa completa (cliente, assistente e atendente, cada um identificado), os terminais e recebíveis do cliente, as notas internas e a trilha de auditoria. As ações SHALL ser **Assumir conversa** (protocolo aberto), **Enviar mensagem** e **Encerrar atendimento** (só para quem assumiu). Encerrar SHALL pedir a nota de resolução antes de confirmar.

#### Scenario: Protocolo assumido por outro
- **WHEN** o atendente abre um protocolo que outro já assumiu
- **THEN** vê a conversa e quem assumiu, sem os botões de enviar e encerrar

#### Scenario: Conflito ao assumir
- **WHEN** o atendente clica em Assumir e outro já assumiu
- **THEN** vê o aviso com o nome de quem assumiu e a tela passa ao modo somente leitura

### Requirement: Chat do cliente em atendimento humano
Enquanto a conversa estiver em modo humano, o chat do cliente SHALL mostrar um aviso fixo com o estado ("Aguardando um atendente" ou "Você está falando com <nome>") e o protocolo, SHALL buscar novas mensagens periodicamente e SHALL exibir as mensagens do atendente com o selo "Atendente <nome>", distintas das do assistente. Ao encerrar, o aviso SHALL sumir e o aviso de encerramento SHALL aparecer na conversa.

#### Scenario: Resposta do atendente chega ao cliente
- **WHEN** o atendente envia uma mensagem
- **THEN** ela aparece no chat do cliente em poucos segundos, com o selo do atendente, sem recarregar a página

#### Scenario: Recarregar a página em modo humano
- **WHEN** o cliente recarrega o chat durante o atendimento humano
- **THEN** o aviso de atendimento e todas as mensagens do atendente continuam visíveis
