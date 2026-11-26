# Spec Delta

## Purpose

Define quem é atendente, como ele se autentica na área de atendimento e o que o papel `attendant` pode ver e fazer, mantendo o isolamento por workspace.

## ADDED Requirements

### Requirement: Login de atendente no perfil de demonstração
No perfil `local-demo`, o sistema SHALL autenticar atendentes cadastrados na carga de demonstração por e-mail e senha, emitindo um token de sessão assinado pelo servidor com validade limitada. As senhas SHALL ser guardadas apenas como hash. Credenciais inválidas SHALL receber a mesma resposta, sem indicar se o identificador existe.

#### Scenario: Login válido
- **WHEN** um atendente cadastrado envia identificador e senha corretos
- **THEN** recebe um token de sessão, seu nome de exibição e a data de expiração

#### Scenario: Credencial inválida
- **WHEN** o e-mail não existe ou a senha está errada
- **THEN** a resposta é `401` com a mesma mensagem nos dois casos

#### Scenario: Token expirado ou adulterado
- **WHEN** uma rota de atendente recebe um token vencido ou com assinatura inválida
- **THEN** a resposta é `401` e nenhuma ação é executada

### Requirement: Contas de demonstração visíveis
No perfil `local-demo`, o sistema SHALL expor, sem autenticação, a lista das contas de atendente de demonstração (nome, e-mail e senha de teste), para que a tela de login possa preenchê-las. Fora de `local-demo`, essa lista SHALL não existir.

#### Scenario: Listar contas de demonstração
- **WHEN** o frontend pede as contas de demonstração no perfil `local-demo`
- **THEN** recebe Ana e Bruno com e-mail e senha de teste

#### Scenario: Contas de demonstração em produção
- **WHEN** a mesma rota é chamada no perfil `production`
- **THEN** a resposta é `404`

### Requirement: Papel do atendente em produção
No perfil `production`, o papel `attendant` SHALL vir do token OIDC já verificado, e o login por senha SHALL ficar indisponível.

#### Scenario: Login por senha em produção
- **WHEN** alguém chama o login de atendente no perfil `production`
- **THEN** a resposta é `404` ou `503`, sem verificar credenciais

### Requirement: Escopo do papel attendant
Um atendente SHALL acessar apenas protocolos, conversas e dados de suporte do seu workspace, e apenas pelas rotas de atendente. A identidade do cliente atendido SHALL vir do protocolo, nunca de um parâmetro enviado pelo atendente. Rotas do cliente e de administração SHALL recusar o token de atendente.

#### Scenario: Protocolo de outro workspace
- **WHEN** um atendente pede um protocolo de outro workspace
- **THEN** a resposta é `404`, como se o protocolo não existisse

#### Scenario: Atendente em rota de administração
- **WHEN** um token de atendente chama uma rota de administração de agentes
- **THEN** a resposta é `401` ou `403`

#### Scenario: Cliente em rota de atendente
- **WHEN** a identidade do cliente chama uma rota de atendente
- **THEN** a resposta é `401` ou `403`
