# Spec Delta

## Purpose

Permitir administrar agentes, prompts e conexões de modo controlado, auditável e reproduzível.

## ADDED Requirements

### Requirement: Cadastro e edição de agentes
O sistema SHALL permitir a administradores criar e editar rascunhos com nome, descrição, instruções, ferramentas e agentes delegáveis.

#### Scenario: Alteração em rascunho
- **WHEN** um administrador salva alterações válidas em um rascunho
- **THEN** o rascunho é persistido sem alterar a configuração publicada

### Requirement: Versionamento e publicação
O sistema SHALL manter versões imutáveis publicadas de agentes e prompts com autor, data, estado, comparação e restauração por nova publicação.

#### Scenario: Restaurar versão anterior
- **WHEN** o administrador restaura uma versão anterior
- **THEN** uma nova versão é publicada com o conteúdo selecionado e o histórico permanece íntegro

### Requirement: Validação do grafo
O sistema SHALL impedir a publicação de vínculos com agentes inexistentes, desativados, auto-relacionamentos ou ciclos, e aplicar validação concorrente no servidor.

#### Scenario: Ciclo detectado
- **WHEN** uma publicação criaria A → B → A
- **THEN** a publicação é recusada com o caminho do ciclo e a versão publicada anterior permanece ativa

### Requirement: Consulta de relacionamentos
O sistema SHALL oferecer endpoint autorizado de consulta de relações diretas de entrada e saída e validação de vínculos propostos, distinguindo rascunho e publicado.

#### Scenario: Consultar agentes vinculados
- **WHEN** o administrador consulta um agente publicado
- **THEN** recebe seus destinos e origens com identificadores, nomes, estado e versão associada

### Requirement: Conflito de edição
O sistema SHALL rejeitar gravações concorrentes baseadas em versão antiga e informar ao usuário que existe uma versão mais recente.

#### Scenario: Edição concorrente
- **WHEN** duas sessões salvam sobre a mesma revisão e a segunda usa versão desatualizada
- **THEN** a segunda recebe conflito e mantém seus dados editados para comparação
