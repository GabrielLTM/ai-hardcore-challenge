# Spec Delta

## Purpose

Define como a resposta final de uma execução é julgada por um agente Revisor, quando ela é refeita, como isso aparece para o cliente e para a operação, e como o mesmo julgamento entra na avaliação.

## ADDED Requirements

### Requirement: Revisão da resposta com evidência
Quando o grafo publicado tem um Revisor ativo e a execução recuperou evidência ou chamou uma ferramenta de dados do cliente, o sistema SHALL submeter a resposta final do Roteador ao Revisor antes de concluir a execução. O Revisor SHALL receber a pergunta, a resposta, as evidências recuperadas e as citações inválidas já detectadas. O Revisor SHALL devolver um veredito `aprovar` ou `revisar`, com nota de 0 a 2 para fundamentação, cobertura, limitações e segurança, e uma lista de problemas.

#### Scenario: Resposta fundamentada
- **WHEN** a resposta cita evidências que sustentam suas afirmações e responde a toda a pergunta
- **THEN** o veredito é `aprovar` e a execução conclui com essa resposta

#### Scenario: Resposta sem evidência nem ferramenta
- **WHEN** a execução termina sem evidência recuperada e sem ferramenta de dados chamada (saudação, recusa, fora de escopo)
- **THEN** o Revisor não é chamado e a execução registra que a revisão foi dispensada

#### Scenario: Revisor desligado
- **WHEN** o grafo publicado não tem Revisor ativo
- **THEN** a execução conclui como antes, sem eventos de revisão

### Requirement: Replanejamento limitado
Com veredito `revisar`, o sistema SHALL executar no máximo uma nova rodada do Roteador. Essa rodada recebe a pergunta, a resposta anterior e os problemas apontados, e pode delegar e buscar evidência de novo. A nova rodada SHALL respeitar os limites de tokens, tempo, delegações e ferramentas da execução, que valem para a execução inteira. A segunda resposta SHALL ser julgada pelo Revisor só para registro e SHALL ser entregue mesmo que o veredito continue `revisar`.

#### Scenario: Revisão aceita
- **WHEN** o Revisor devolve `revisar` e há orçamento
- **THEN** o Roteador produz nova resposta, ela substitui a anterior e a execução registra os dois vereditos

#### Scenario: Orçamento esgotado
- **WHEN** o Revisor devolve `revisar` e o orçamento de tokens ou de tempo não comporta nova rodada
- **THEN** a primeira resposta é entregue e a execução registra que a revisão não foi feita por limite

#### Scenario: Segunda reprovação
- **WHEN** a resposta revisada também recebe `revisar`
- **THEN** ela é entregue sem terceira rodada

### Requirement: Falha do Revisor não bloqueia a resposta
Se o Revisor falhar (erro do modelo, saída fora do esquema, tempo esgotado), o sistema SHALL entregar a resposta do Roteador e SHALL registrar a falha da revisão. A execução não falha por causa do Revisor.

#### Scenario: Saída inválida do Revisor
- **WHEN** o Revisor devolve texto que não segue o esquema do veredito
- **THEN** a resposta original é entregue e a trilha registra a falha da revisão

### Requirement: Resposta retida até o veredito
Quando o grafo tem Revisor ativo e a execução já recuperou evidência ou chamou ferramenta de dados, o sistema SHALL reter o texto da resposta do Roteador e só transmiti-lo ao cliente depois do veredito. O cliente SHALL receber só a resposta aprovada, a refeita, ou a original quando a revisão falhou ou não coube no orçamento. Texto gerado antes de haver evidência (saudação, recusa) SHALL continuar sendo transmitido enquanto é gerado.

#### Scenario: Resposta revisada não chega ao cliente
- **WHEN** a primeira resposta com evidência recebe `revisar` e há orçamento
- **THEN** nenhum `response.delta` da primeira resposta é transmitido; o SSE traz `review.started`, `review.completed` e depois só o texto da resposta refeita

#### Scenario: Resposta aprovada
- **WHEN** o veredito é `aprovar`
- **THEN** o texto é transmitido depois de `review.completed`

#### Scenario: Saudação sem evidência
- **WHEN** a execução termina sem evidência nem ferramenta de dados
- **THEN** o texto é transmitido enquanto é gerado e a revisão é dispensada

### Requirement: Eventos de revisão
O sistema SHALL emitir `review.started` e `review.completed` na trilha e no SSE, com veredito, notas, rodada, problemas resumidos e custo, nunca com texto de evidência. Se algum texto já tiver sido transmitido e a revisão o substituir, o sistema SHALL emitir `response.reset` antes do novo texto. A mensagem gravada na conversa SHALL ser só a resposta final.

#### Scenario: Reconexão durante a verificação
- **WHEN** o cliente reconecta ao SSE com `Last-Event-ID` antes de o texto retido ser transmitido
- **THEN** recebe os eventos de revisão e, depois, só o texto final

### Requirement: Chat mostra a verificação
Enquanto a resposta está retida, o chat SHALL mostrar o andamento (consultas, "Verificando a resposta") e SHALL exibir o texto quando ele chegar, sem duplicar conteúdo, inclusive ao reconectar.

#### Scenario: Indicador de verificação
- **WHEN** chega `review.started`
- **THEN** o chat mostra "Verificando a resposta" até o texto chegar ou a execução terminar

### Requirement: Evidência da busca web verificável
O sistema SHALL guardar, como texto da evidência de cada fonte web, o conteúdo que a busca web devolveu ao agente, limitado em tamanho, para que o Revisor possa conferir a resposta contra ele. Esse texto SHALL não aparecer nos eventos do SSE.

#### Scenario: Resposta baseada na web
- **WHEN** o Revisor julga uma resposta que cita uma fonte web
- **THEN** a evidência que ele recebe inclui o texto devolvido pela busca, além de título e URL

### Requirement: Curadoria dos vereditos
O sistema SHALL oferecer a administradores uma visão agregada dos vereditos de um período, calculada sem nova chamada ao modelo, com padrões de erro e sugestões de ajuste derivadas deles. A visão SHALL ficar restrita ao workspace do administrador e SHALL recusar quem não é administrador.

#### Scenario: Padrões de um período
- **WHEN** um administrador abre a curadoria dos últimos 7 dias
- **THEN** vê os totais julgados e revisados, os critérios com mais notas abaixo de 2, a taxa de revisão por agente envolvido, as buscas na base que voltaram vazias (com a consulta), exemplos de respostas revisadas (pergunta, problemas, fontes, link para a conversa) e sugestões de ajuste

#### Scenario: Cliente na curadoria
- **WHEN** uma identidade que não é administradora chama a curadoria
- **THEN** a resposta é `403`

### Requirement: Métricas do Revisor
O sistema SHALL registrar métricas de vereditos por resultado e rodada, de revisões dispensadas, feitas e não feitas por limite, de falhas do Revisor e de latência da revisão. As métricas SHALL não ter atributos de cliente, conversa ou texto. O dashboard provisionado SHALL mostrar a taxa de revisão, a distribuição de vereditos e a latência da revisão.

#### Scenario: Execução revisada
- **WHEN** uma execução passa por revisão e nova rodada
- **THEN** as métricas contam um veredito `revisar` na rodada 1 e o veredito da rodada 2

### Requirement: Avaliação semântica pelo Revisor
O relatório de avaliação SHALL incluir, por caso, os vereditos e notas do Revisor, e no agregado a taxa de aprovação na primeira rodada, a taxa de revisão, a média das notas por critério e a fração de revisões em que as notas melhoraram.

#### Scenario: Relatório com julgamento
- **WHEN** `run_eval` roda o dataset
- **THEN** o relatório em JSON e Markdown traz as notas do Revisor por caso e os agregados

### Requirement: Calibração do Revisor
O sistema SHALL ter um dataset versionado de respostas rotuladas: corretas e com defeitos conhecidos (número inventado, citação que não sustenta, parte da pergunta ignorada, limitação omitida, vazamento). Também SHALL ter um executor que mede a detecção de defeitos (recall de `revisar`) e a aprovação das respostas corretas (especificidade). A execução com modelo real SHALL exigir limite explícito de casos e de tokens.

#### Scenario: Calibração com orçamento
- **WHEN** o executor de calibração roda com modelo real sem limite de tokens
- **THEN** recusa iniciar e explica o parâmetro que falta

#### Scenario: Defeito detectado
- **WHEN** uma resposta com número inventado sem citação é julgada
- **THEN** o resultado conta como detecção correta se o veredito for `revisar` com nota de fundamentação abaixo de 2
