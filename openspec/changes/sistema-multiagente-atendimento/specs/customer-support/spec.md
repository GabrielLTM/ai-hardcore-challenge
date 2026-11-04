# Spec Delta

## Purpose

Apoiar atendimentos personalizados por meio de consultas controladas a dados demonstrativos do cliente.

## ADDED Requirements

### Requirement: Consulta de recebíveis
O suporte SHALL disponibilizar ferramenta de recebíveis com vendas, situação e previsão baseada em dados persistidos do cliente autorizado.

#### Scenario: Venda de ontem disponível
- **WHEN** o usuário solicita a previsão de recebimento e existem dados para sua conta
- **THEN** a ferramenta retorna os dados aplicáveis e a resposta informa que são dados de demonstração

### Requirement: Consulta de terminal
O suporte SHALL disponibilizar ferramenta de terminal com modelo, estado de conectividade e erros disponíveis para o cliente autorizado.

#### Scenario: Terminal com falha
- **WHEN** o usuário relata problema e há um terminal associado
- **THEN** o agente consulta o estado persistido e pode buscar orientação relacionada em fontes do knowledge

### Requirement: Dados ausentes
O suporte SHALL informar ausência ou insuficiência de dados sem inventar previsões, diagnósticos ou a existência de integrações reais.

#### Scenario: Cliente sem recebíveis
- **WHEN** não existem recebíveis associados ao usuário
- **THEN** a resposta informa a ausência e não fabrica data ou valor

### Requirement: Identidade das ferramentas
O sistema SHALL obter a identidade autorizada do contexto do backend e impedir que argumentos produzidos pelo modelo selecionem outro cliente.

#### Scenario: Tentativa de troca de usuário
- **WHEN** o modelo solicita dados com identificador diferente do contexto autorizado
- **THEN** a consulta é recusada ou substituída pelo identificador autorizado e o outro cliente não é revelado
