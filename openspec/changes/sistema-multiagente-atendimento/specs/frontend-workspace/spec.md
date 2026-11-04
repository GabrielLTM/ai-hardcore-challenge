# Spec Delta

## Purpose

Definir uma interface que permita conversar, inspecionar evidências e administrar agentes com clareza e acessibilidade.

## ADDED Requirements

### Requirement: Chat e execução visível
O frontend SHALL apresentar histórico, resposta progressiva, estado de geração, opção de parar e linha do tempo com agentes e ferramentas acionados.

#### Scenario: Geração em andamento
- **WHEN** o usuário envia uma mensagem
- **THEN** vê a resposta crescer e as etapas com seus estados sem perder acesso à opção de cancelamento

### Requirement: Inspeção de evidências
O frontend SHALL permitir abrir fontes e trechos recuperados com título, URL, data e limitações, distinguindo resultados recuperados de fontes efetivamente citadas.

#### Scenario: Abrir evidências
- **WHEN** o usuário seleciona as fontes de uma resposta
- **THEN** vê o conteúdo recuperado relacionado e consegue abrir a origem original

### Requirement: Administração de agentes e versões
O frontend SHALL oferecer listagem, edição de rascunho, vínculos, validação, publicação, comparação e histórico de agentes e prompts conforme permissões.

#### Scenario: Publicação inválida
- **WHEN** o administrador tenta publicar configuração com ciclo
- **THEN** o formulário preserva o rascunho e apresenta os vínculos que precisam ser corrigidos

### Requirement: Estados e recuperação
O frontend SHALL apresentar estados vazio, carregando, falha, parcial, cancelado, desconectado e conflito, preservando mensagens e edições recuperáveis.

#### Scenario: Desconexão durante streaming
- **WHEN** a conexão é interrompida durante a geração
- **THEN** o conteúdo já recebido permanece visível e o usuário vê o estado de reconexão ou interrupção

### Requirement: Acessibilidade e responsividade
O frontend SHALL permitir uso por teclado e tecnologias assistivas, respeitar movimento reduzido e adaptar chat e inspeção a telas estreitas.

#### Scenario: Navegação por teclado
- **WHEN** o usuário navega pelos campos, timeline e diálogo de fontes usando teclado
- **THEN** os controles têm foco visível, rótulos acessíveis e ordem previsível
