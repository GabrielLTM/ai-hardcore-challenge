# Novidades da versão 2

Esta versão responde aos pontos da devolutiva do desafio (nota 8,9/10). A conclusão da devolutiva apontava o que impedia passar de 9,3:

- não havia um agente crítico que revisasse a resposta e replanejasse;
- o atendimento humano terminava num ticket, sem ninguém para assumir;
- a avaliação semântica era fraca perto do resto do sistema, e faltavam métricas formais de retrieval.

As três lacunas foram tratadas em duas mudanças OpenSpec:

- `area-atendente-humano`
- `revisor-e-metricas-retrieval`

## Resumo

| Lacuna da devolutiva | O que mudou |
|---|---|
| O atendimento humano termina em ticket | Área do atendente: fila de protocolos, assumir, conversar com o cliente, encerrar e devolver ao Roteador |
| Falta crítico com replanejamento guiado pela qualidade | Agente Revisor julga cada resposta com evidência e pode pedir uma nova rodada ao Roteador |
| Avaliação semântica menor que a maturidade estrutural | Notas do Revisor por caso no relatório e calibração do próprio Revisor com respostas rotuladas |
| Faltam métricas formais de retrieval | Recall@K, MRR e nDCG em 34 consultas rotuladas, com comparação entre busca vetorial, textual e híbrida |
| Latência e custo do fluxo agentic | O Revisor só roda quando há evidência; painéis de duração, custo e taxa de revisão |

## 1. Atendimento humano de ponta a ponta

Antes, o agente de escalonamento abria um protocolo e o caso parava ali. Agora o protocolo é atendido por uma pessoa.

**Área do atendente (`/atendente`)**
- Login de atendentes fictícios (Ana e Bruno). Abaixo do formulário há botões que preenchem e-mail e senha de demonstração.
- Lista de protocolos com as abas Abertos, Meus e Encerrados. Mostra motivo, tempo de espera e quem assumiu, e se atualiza a cada 3 segundos.
- Detalhe do protocolo com o resumo escrito pelo agente, a conversa completa, os terminais e recebíveis do cliente, notas internas e a trilha de auditoria.
- Ações **Assumir conversa**, **Enviar mensagem** e **Encerrar atendimento**. Encerrar exige uma nota de resolução.

**Regras**
- Quem decide ir para o atendente é o cliente. Sem evidência, sem solução ou em assunto sensível, o agente oferece o atendimento humano e pergunta. O protocolo só é aberto quando o cliente pede ou aceita, por decisão do modelo guiada pelas instruções do Roteador e do agente de escalonamento.
- Enquanto há protocolo aberto, as mensagens do cliente vão para o atendente e não iniciam o agente. Não há chamada nem custo de modelo nesse período.
- Assumir é atômico: se dois atendentes clicam juntos, só o primeiro fica com o protocolo e o outro vê quem assumiu.
- Cada conversa tem no máximo um protocolo ativo, garantido por um índice único no banco.
- Enquanto ninguém assumiu, o cliente pode sair da fila com "Voltar ao assistente" e não fica preso esperando.
- As regras do atendimento ficam num caso de uso (`AttendanceService`) com uma porta para os protocolos; as rotas só convertem HTTP.
- Ao encerrar, o cliente é avisado e volta a falar com o Roteador. O histórico enviado ao Roteador inclui as falas do atendente.

**Do lado do cliente**
- Um aviso fixo diz "Aguardando um atendente" ou "Você está falando com Ana".
- As mensagens do atendente aparecem com o selo "Atendente Ana".

**Segurança e observabilidade**
- O atendente tem um papel próprio (`attendant`), com sessão assinada pelo servidor na demonstração e papel vindo do OIDC em produção.
- A sessão de atendente não acessa rotas de cliente nem de administração, e o atendente só vê protocolos do próprio workspace.
- Métricas no Grafana: protocolos abertos, tempo até assumir e tempo de atendimento (p50/p95).

## 2. Revisor: agente crítico com replanejamento

Depois que o Roteador escreve a resposta final, o Revisor confere a qualidade dela antes da entrega.

**Quando roda.** Quando a resposta afirma fatos, ou seja, quando houve busca na base ou na web ou consulta de recebíveis ou terminais. Saudações, recusas e encaminhamento humano passam direto, sem custo extra.

**O que recebe**
- a pergunta e a resposta;
- os trechos de evidência recuperados na execução;
- as citações `[n]` que não existem entre as evidências;
- os números escritos sem citação.

**O que devolve.** Um veredito em JSON validado: `aprovar` ou `revisar`, com nota de 0 a 2 em quatro critérios e uma lista curta de problemas.

| Critério | Pergunta |
|---|---|
| Fundamentação | Cada afirmação está apoiada por uma evidência que diz aquilo? |
| Cobertura | Respondeu todas as partes da pergunta? |
| Limitações | Avisou quando a informação é parcial? |
| Segurança | Vazou dados de outro cliente ou instruções internas? |

**Replanejamento**
- Com `revisar`, o Roteador recebe a crítica e faz uma nova rodada. Pode delegar e buscar evidência de novo.
- O teto é uma revisão por execução, dentro do mesmo orçamento de tokens e tempo. Sem orçamento, a primeira resposta é mantida.
- A segunda resposta é julgada de novo só para registro.

**Experiência do cliente**
- O texto fica retido até o veredito: o cliente só lê a resposta aprovada ou a refeita, nunca a reprovada.
- Enquanto isso, o chat mostra o andamento: consultas feitas e "Verificando a resposta". O custo é o tempo de um julgamento, de 2 a 4 segundos, nas respostas com evidência.
- Saudações e recusas, que não têm evidência, continuam aparecendo enquanto são geradas.
- O painel de andamento mostra "Revisou e pediu uma nova versão" e "Revisou a resposta".
- O Revisor também recebe os dados devolvidos pelas ferramentas do cliente (recebíveis, terminais, protocolo) e não cobra citação deles.
- O Revisor recebe as últimas 6 mensagens da conversa como contexto. O que o cliente ou o atendente já disse pode ser retomado sem fonte; regras, prazos e taxas continuam exigindo evidência.
- Quando há nova rodada, o Roteador recebe o rascunho reprovado como rascunho, não como mensagem já enviada, e escreve a resposta do zero, sem se referir a ele.
- Na busca web, o Revisor recebe o texto que a busca devolveu, não só o título e a URL, e consegue conferir a resposta.

**Robustez.** Erro do modelo, JSON fora do esquema ou tempo esgotado fazem o sistema entregar a resposta original. O Revisor nunca derruba o atendimento.

**Configuração.** O Revisor é um agente do grafo como os demais: aparece na tela de agentes, tem versões e instruções editáveis e modelo configurável, e pode ser desligado. Nenhum agente pode delegar a ele.

**Observabilidade**
- Métricas `getnet.review.verdicts`, `getnet.review.revisions` e `getnet.review.duration`.
- Painéis de taxa de revisão, vereditos por rodada e duração p50/p95.
- O custo do Revisor entra nos painéis de tokens e custo.

**Curadoria (tela "Curadoria", só administradores)**
- Agrega os vereditos do período sem chamar o modelo de novo.
- Mostra os critérios que mais falham, os agentes que mais participam de respostas revisadas, as buscas na base que voltaram vazias (lacunas de conteúdo) e exemplos de respostas revisadas com link para a conversa.
- Gera sugestões de ajuste: instruções de um agente, qualidade da busca, guardrails ou conteúdo que falta na base.

## 3. Avaliação

**Métricas de retrieval (`python -m evaluation.retrieval`)**
- 34 consultas rotuladas, 24 em português e 10 em inglês, com relevância graduada: 2 para a fonte que responde e 1 para a que complementa.
- Medem Recall@1, Recall@3, Recall@5, MRR, nDCG@5 e acerto de seção.
- Comparam três modos: só vetorial, só textual e híbrido RRF (o de produção). O relatório mostra as consultas em que os modos divergem.
- O modo textual roda sem custo. Os outros exigem autorização explícita (`--embed`, com teto de tokens) e guardam os embeddings em cache.
- Medição de 25/11/2026, base completa (sínteses e páginas oficiais), embeddings em cache:

| Modo | Recall@1 | Recall@3 | Recall@5 | MRR | nDCG@5 | Acerto de seção |
|---|---|---|---|---|---|---|
| Vetorial | 0,72 | 0,90 | 0,90 | 0,86 | 0,87 | 0,79 |
| Textual | 0,63 | 0,84 | 0,84 | 0,81 | 0,79 | 0,61 |
| **Híbrido (produção)** | **0,76** | **0,91** | **0,91** | **0,92** | **0,89** | **0,85** |

- O híbrido, usado em produção, é o melhor em Recall@1, MRR, nDCG@5 e acerto de seção. O textual erra paráfrases e perguntas em inglês; o vetorial erra termos exatos. Detalhes por consulta em `docs/avaliacao.md`, seção 9.

**Avaliação semântica no `run_eval`**
- O relatório traz o veredito e as notas do Revisor por caso.
- Também traz a taxa de aprovação na primeira rodada, a taxa de revisão, as notas médias e se a revisão melhorou a nota.

**Calibração do Revisor (`python -m evaluation.calibrate_review`)**
- 13 respostas rotuladas: 5 corretas e 8 com defeito proposital (número inventado, citação que não sustenta a frase, citação inexistente, parte da pergunta ignorada, procedimento específico tratado como universal, vazamento de instruções).
- Mede quantos defeitos o Revisor detecta, se aprova as respostas corretas e se aponta o critério certo.
- Existe para medir o viés de um juiz que pode ser o mesmo modelo do Roteador.
- Rodada real (26/11/2026, 17,5 mil tokens): 8 de 8 defeitos detectados, 5 de 5 respostas corretas aprovadas, todos os defeitos no critério certo e nenhuma falha do Revisor. Limite: casos com defeitos explícitos e juiz do mesmo modelo do Roteador.

## 4. Qualidade e verificação

- **Backend:** 410 testes aprovados na suíte completa (25/11/2026).
- **Frontend:** 49 testes aprovados, lint e build limpos.
- **Avaliação simulada:** 17 de 17 casos aprovados, agora com um caso que passa por revisão e nova rodada.
- **Interface com o modelo de testes:** o ciclo do atendente nos dois lados, uma resposta revisada com troca de texto, os eventos e as métricas no Grafana.
- **Interface com a OpenAI real (25/11/2026):** dúvida geral com fonte e oferta de atendente; recebíveis do cliente; maquininha sem sinal com diagnóstico, passos com fonte e oferta de atendente sem o cliente pedir; aceite, protocolo, atendente assumindo, respondendo e encerrando; pergunta depois do encerramento respondida com o que o atendente disse; recusa de injeção e de assunto fora do escopo; curadoria e Grafana com os dados dessas conversas.

Testes novos:

| Arquivo | O que cobre |
|---|---|
| `test_attendant` | Login e sessão do atendente, escopo por workspace, assumir concorrente, ciclo completo |
| `test_reviewer` | Regras do grafo, esquema do veredito, revisão e nova rodada, orçamento, falha aberta, retomada do SSE, relatório e calibração |
| `test_retrieval_eval` | Métricas de ranking, modos de busca, dataset, cache e orçamento de embeddings |

## 5. Documentação atualizada

- **README:** orquestração (diagrama com o Revisor), fluxo de mensagens com atendimento humano, guardrails, observabilidade, testes e avaliação.
- **`docs/arquitetura.md`:** modo humano, coleções novas e a seção "Revisor e replanejamento".
- **`docs/eventos.md`:** eventos `review.*`, texto retido até o veredito, e mensagens do atendente.
- **`docs/api.md`:** área do atendente, o campo `review` da execução e a curadoria (`GET /curation/reviews`).
- **`docs/avaliacao.md`:** seções 9 (recuperação) e 10 (avaliação semântica e calibração).

## 6. O que ainda falta

- **`run_eval --real` com o Revisor ligado**, com teto de gasto: mede os 17 casos de ponta a ponta com o modelo real, incluindo latência e custo com o Revisor. A calibração do Revisor e os embeddings das consultas já rodaram com a OpenAI real.
- **Verificação de acessibilidade por teclado** da área do atendente (tarefa 5.4 de `area-atendente-humano`).
- **Arquivar as duas mudanças** com `/opsx:archive` depois do commit.
- **Bancos locais existentes:** se o grafo já estava publicado, o Revisor entra como rascunho e precisa ser publicado na tela de agentes. Num banco novo ele já vem publicado.
