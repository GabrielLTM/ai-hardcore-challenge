# Spec Delta

## Purpose

Coordenar agentes especializados e ferramentas com rastreabilidade e limites de execução observáveis.

## ADDED Requirements

### Requirement: Roteamento e cooperação
O sistema SHALL disponibilizar router, knowledge e support com responsabilidades distintas e permitir ao router coordenar um ou mais especialistas configurados.

#### Scenario: Solicitação de atendimento com consulta de conhecimento
- **WHEN** uma solicitação exige dados do cliente e instruções de produto
- **THEN** a execução consulta o suporte e pode delegar ao knowledge conforme os vínculos publicados, apresentando a resposta consolidada

### Requirement: Delegação autorizada
O sistema SHALL validar no backend o destino, o vínculo publicado e as ferramentas permitidas antes de executar qualquer delegação.

#### Scenario: Destino não permitido
- **WHEN** um agente solicita delegação para destino ausente, desativado ou não vinculado
- **THEN** a chamada é recusada e registrada como erro sem executar o agente

### Requirement: Limites e estados de execução
O sistema SHALL aplicar limites configuráveis de profundidade, delegações, chamadas, tempo e tokens por execução e expor eventos de início, conclusão e falha.

#### Scenario: Limite alcançado
- **WHEN** uma execução atinge um limite configurado
- **THEN** nenhuma nova delegação é iniciada e o usuário recebe uma conclusão parcial ou erro identificável

### Requirement: Snapshot de execução
O sistema SHALL fixar no início da execução as versões publicadas dos agentes, prompts, ferramentas e relacionamentos utilizados.

#### Scenario: Configuração alterada durante execução
- **WHEN** uma versão é publicada enquanto uma conversa está gerando resposta
- **THEN** a execução em andamento mantém seu snapshot e uma nova execução usa a nova versão

### Requirement: Conteúdo externo como evidência
O sistema SHALL tratar mensagens, resultados de ferramentas e documentos recuperados como dados não confiáveis, sem conceder permissões ou alterar instruções superiores.

#### Scenario: Instrução maliciosa em documento
- **WHEN** um trecho recuperado pede para chamar uma ferramenta não permitida
- **THEN** o sistema não altera as permissões por causa desse trecho
