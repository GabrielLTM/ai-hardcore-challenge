# Spec Delta

## Purpose

Fundamentar respostas em conteúdo recuperado de fontes oficiais e disponibilizar pesquisa atualizada na web.

## ADDED Requirements

### Requirement: Ingestão reproduzível
O sistema SHALL ingerir URLs oficiais e os documentos curados existentes, extraindo e dividindo conteúdo, gerando embeddings e preservando proveniência, data, versão e limitações.

#### Scenario: Reingestão idempotente
- **WHEN** a mesma fonte e conteúdo são ingeridos novamente com a mesma versão de processamento
- **THEN** não são criadas cópias duplicadas e a versão indexada permanece rastreável

#### Scenario: Origem indisponível
- **WHEN** a coleta de uma fonte falha
- **THEN** o trabalho registra a falha sem marcar conteúdo antigo como atualizado nem substituir a última versão válida

### Requirement: Recuperação híbrida
O sistema SHALL combinar busca semântica e textual e disponibilizar trechos relevantes com sua origem e versão para o agente de conhecimento.

#### Scenario: Busca por produto específico
- **WHEN** uma pergunta contém nome de produto e descrição semântica
- **THEN** a recuperação considera ambos os sinais e devolve trechos elegíveis com URL e identificador

### Requirement: Evidência na resposta
O sistema SHALL apresentar ao usuário os trechos efetivamente usados, documentos, URLs, data de coleta e limitações, sem alegar que foram usados trechos não recuperados.

#### Scenario: Inspeção de fontes
- **WHEN** o usuário abre os detalhes de uma resposta fundamentada
- **THEN** vê os trechos efetivamente retornados, suas fontes e a indicação de uso pela resposta quando disponível

### Requirement: Ausência de evidência
O sistema SHALL informar insuficiência ou conflito de evidências quando não houver suporte confiável e evitar inventar condições comerciais ou dados do cliente.

#### Scenario: Fontes divergentes de Pix
- **WHEN** os trechos descrevem prazos para modalidades diferentes sem identificar a do usuário
- **THEN** o sistema pede contexto ou explica o escopo, sem prometer um prazo único

### Requirement: Busca web atual
O sistema SHALL oferecer pesquisa web ao agente de conhecimento para perguntas gerais e atuais, exibindo fontes e tratando indisponibilidade.

#### Scenario: Pergunta sobre previsão do tempo
- **WHEN** a pergunta requer previsão atual
- **THEN** a aplicação consulta a ferramenta web, responde com fonte e data quando disponíveis ou informa falha
