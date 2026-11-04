# Spec Delta

## Purpose

Tornar o comportamento de atendimento e seus custos rastreáveis por execução. Decisão de escopo (04/11/2026): OpenTelemetry e Grafana ficam fora para manter o núcleo enxuto e explicável; é evolução futura.

## ADDED Requirements

### Requirement: Correlação por identificadores
O sistema SHALL gerar um `trace_id` (id de correlação) por execução, devolvê-lo em `POST /chat` e `GET /runs/{id}`, e devolver `X-Request-ID` em toda resposta, incluindo-o nos erros `{code,message,request_id}`.

#### Scenario: Localizar uma execução
- **WHEN** o operador informa o `trace_id` ou o `request_id` de uma resposta
- **THEN** é possível localizar a execução e o erro correspondentes

### Requirement: Tokens e custo estimado por execução
O sistema SHALL registrar os tokens informados pelo provedor e o custo estimado por execução, com tabela de preços versionada (`PRICING_VERSION`), identificando valores ausentes.

#### Scenario: Uso informado pelo provedor
- **WHEN** uma chamada ao modelo retorna uso de tokens e o modelo tem preço conhecido
- **THEN** a aplicação registra o uso e calcula custo estimado com a versão da tabela

#### Scenario: Custo desconhecido
- **WHEN** o modelo não tem preço conhecido ou o provedor não retorna contagem de tokens
- **THEN** o custo da execução é desconhecido (`null`, exibido como não informado) e nunca é registrado como zero

### Requirement: Logs sem conteúdo sensível
O sistema SHALL evitar segredos e conteúdo de mensagens nos logs, nos eventos e nas respostas de erro.

#### Scenario: Credencial presente na requisição
- **WHEN** ocorre erro de provedor
- **THEN** a credencial e o detalhe do provedor não aparecem em logs, eventos ou respostas de erro
