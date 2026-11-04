# Spec Delta

## Purpose

Proteger dados e operações por identidade e estabelecer limites operacionais para crescimento do serviço.

## ADDED Requirements

### Requirement: Identidade e escopo
O sistema SHALL associar conversas, execuções e dados de suporte a uma identidade e um workspace autorizados no servidor.

#### Scenario: Acesso cruzado
- **WHEN** um usuário solicita conversa ou execução de outro workspace sem permissão
- **THEN** o servidor nega o acesso sem divulgar seu conteúdo

### Requirement: Perfis de autenticação
O sistema SHALL disponibilizar perfil local de demonstração explicitamente marcado e um contrato de autenticação para implantação multiusuário; perfil local SHALL NOT ser ativado implicitamente em produção.

#### Scenario: Implantação sem autenticação configurada
- **WHEN** a aplicação inicia em perfil de produção sem provedor de identidade configurado
- **THEN** a inicialização falha com mensagem operacional clara

### Requirement: Autorização administrativa
O sistema SHALL limitar configuração, publicação e consulta administrativa de agentes a usuários autorizados.

#### Scenario: Operador tenta publicar prompt
- **WHEN** identidade sem permissão tenta publicar uma versão
- **THEN** o servidor recusa a operação e mantém a configuração atual

### Requirement: Limites e isolamento de carga
O sistema SHALL limitar concorrência, taxa e orçamento de execução por escopo e devolver erros recuperáveis quando a capacidade for excedida.

#### Scenario: Capacidade esgotada
- **WHEN** o limite configurado de execuções simultâneas é atingido
- **THEN** o sistema recusa novas execuções com orientação de tentativa posterior e preserva as existentes

### Requirement: Múltiplas instâncias
O sistema SHALL manter histórico, versões e estado de execução em armazenamento compartilhado e funcionar com mais de uma instância da API sem depender de afinidade de sessão.

#### Scenario: Reconexão em outra instância
- **WHEN** o cliente reconecta em uma instância diferente
- **THEN** consulta o estado persistido e recupera os eventos disponíveis da execução autorizada
