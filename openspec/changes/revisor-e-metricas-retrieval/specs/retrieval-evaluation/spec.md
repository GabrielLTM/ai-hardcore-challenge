# Spec Delta

## Purpose

Define como a qualidade da recuperação de conhecimento é medida offline: dataset rotulado, métricas de ranking e comparação entre os modos de busca.

## ADDED Requirements

### Requirement: Dataset de recuperação rotulado
O sistema SHALL ter um dataset versionado de consultas, em português e em inglês, sobre a base curada. Cada consulta SHALL ter as fontes relevantes com relevância graduada: 2 para a fonte que responde e 1 para a fonte que complementa. Também pode indicar a seção esperada. O relatório SHALL gravar o hash do dataset usado.

#### Scenario: Dataset inválido
- **WHEN** uma consulta referencia uma fonte que não existe no manifesto da base curada
- **THEN** o executor recusa o dataset e nomeia a consulta e a fonte

### Requirement: Métricas de ranking
O executor SHALL calcular, por consulta e na média, Recall@1, Recall@3, Recall@5, MRR e nDCG@5. O cálculo é por fonte: vários trechos da mesma fonte contam uma vez, na melhor posição. SHALL calcular também o acerto de seção quando o dataset a indica.

#### Scenario: Fonte relevante em segundo lugar
- **WHEN** a primeira fonte relevante aparece na posição 2 do ranking
- **THEN** o recíproco da posição é 0,5, Recall@1 é 0 e Recall@3 conta essa fonte

### Requirement: Ablação dos modos de busca
O executor SHALL medir o mesmo dataset em três modos: somente vetorial, somente textual e híbrido com fusão RRF (o modo de produção). Os modos SHALL usar os mesmos índices e o mesmo limite de resultados. O relatório SHALL mostrar as métricas lado a lado e as consultas em que os modos divergem.

#### Scenario: Comparação entre modos
- **WHEN** o executor roda com os três modos
- **THEN** o relatório traz uma tabela por modo e a lista de consultas em que o híbrido perde para um modo isolado

### Requirement: Custo controlado
O modo textual SHALL rodar sem chamar o provedor de modelos. Os modos vetorial e híbrido SHALL gerar os embeddings das consultas só com autorização explícita na linha de comando. Também SHALL reaproveitar um cache local, por modelo de embedding e texto da consulta, para não pagar de novo em execuções repetidas.

#### Scenario: Sem autorização de embeddings
- **WHEN** o executor é chamado sem a opção que autoriza embeddings e o cache não cobre as consultas
- **THEN** roda apenas o modo textual e informa que os outros dois foram pulados

#### Scenario: Cache completo
- **WHEN** todas as consultas já estão no cache para o modelo configurado
- **THEN** os três modos rodam sem chamar o provedor

### Requirement: Relatório de recuperação
O executor SHALL gravar o relatório em JSON e Markdown junto aos relatórios de avaliação existentes. O relatório inclui o hash do dataset, o modelo de embedding, a versão do índice, os parâmetros de busca e as métricas por modo e por consulta.

#### Scenario: Relatório gravado
- **WHEN** a execução termina
- **THEN** existem os dois arquivos com as métricas e os metadados citados
