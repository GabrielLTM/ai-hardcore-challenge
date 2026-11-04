# Capacidade: baseline medido e critérios de expansão

**Leia isto primeiro.** Tudo abaixo vem de cenários de teste numa máquina de desenvolvimento, com o modelo simulado. Os números descrevem o comportamento desta máquina nestes cenários. **Não são capacidade garantida, não são meta e não são gatilho de provisionamento.** Nenhum teste de soak (horas de carga) foi feito, e o caminho real de recuperação de conhecimento, o modelo real, o custo em tokens e um Mongo de produção não foram medidos.

Dados brutos: `backend/loadtest/results/baseline-run1.json` e `baseline-run2.json`. Código: `backend/loadtest/` (`load.py`, `baseline.py`).

## 1. Como foi medido

### Ambiente (rodadas de 03/11/2026, horário de Brasília)

| Item | Valor |
|---|---|
| Máquina | Apple M1 Pro, 8 núcleos, 16 GiB, macOS 26.5.1 |
| API | `uvicorn` 0.54.0 com **1 worker** no host (não em contêiner), Python 3.12.13, uvloop, FastAPI 0.162.0, PyMongo 4.19.0 |
| Mongo | `mongodb/mongodb-atlas-local:8.0.4` do Compose: **um nó** (replica set de 1 membro, com Search), numa VM do Docker Desktop com 4 CPUs e 3,8 GiB. Sem TLS nem autenticação |
| Gerador de carga | Um único processo Python (asyncio + httpx), na mesma máquina |
| Concorrência de recursos | API, Mongo e gerador disputam a mesma máquina. Os contêineres `api` e `worker` do Compose estavam de pé e o Mongo do Compose é compartilhado com o uso interativo da stack: **esse uso concorrente não foi controlado** e é mais uma fonte de ruído nas medições |
| Banco | Descartável `getnet_loadtest_<data>`, apagado ao final. O banco de desenvolvimento não foi tocado |
| Configuração da API | Padrões: `MAX_ACTIVE_RUNS=20` por workspace (alterado onde indicado), lease 30 s, heartbeat 3 s, manutenção 10 s, `SSE_POLL_SECONDS=0,5`, `SSE_HEARTBEAT_SECONDS=15` |

### Modelo simulado (`MODEL_PROVIDER=fake`)

Nenhuma chamada à OpenAI e nenhum custo. Cada execução segue a estrutura real: roteador, delegação ao especialista, `search_knowledge`, resposta do especialista, resposta final do roteador (4 turnos de modelo). Latência simulada por turno: 400 ms até a primeira saída (`FAKE_MODEL_FIRST_TOKEN_MS`) e 40 trechos de texto com 30 ms entre eles (`FAKE_MODEL_DELTA_MS`, `FAKE_MODEL_DELTAS`), totalizando **cerca de 4,0 s de "tempo de modelo" por execução**. Recuperação e embeddings também são simulados (um trecho fixo, sem Mongo Search). O uso de tokens é informado como desconhecido (`null`), então custo e tokens **não foram medidos**. Cada execução gera 22 eventos persistidos, valor fixo neste modelo.

### Cenários

Cada cliente repete: criar conversa, iniciar execução (`POST .../runs`), consumir o SSE até o evento terminal. A carga é de laço fechado (o cliente só começa outra execução quando termina a anterior), com todos os clientes no mesmo workspace e usuário. Uma tentativa recusada com 429 conta como tentativa e o cliente espera 1 s (não repete). Todas as latências são medidas pelo cliente a partir do `POST`:

- `post 202`: tempo até a API aceitar a execução.
- `1º evento`: até o primeiro evento SSE (`run.started`).
- `1º delta`: até o primeiro texto da resposta (`response.delta`).
- `término`: até o evento terminal.

Há um aquecimento descartado antes de cada instância medida. Com poucas amostras, p95 e p99 são praticamente o maior valor (o p99 de 15 amostras é o máximo): trate-os como indicativos. Os resultados de CPU e memória do Mongo vêm de `docker stats` (percentual de **um** núcleo, a VM tem 4) amostrado a cada cerca de 2 s; a memória do contêiner (1,4 a 1,6 GiB) inclui o cache do Mongo e o processo de Search e já estava em ~1,35 GiB em repouso, então não é custo da carga.

### Reprodução

```sh
docker compose up -d mongo                  # Mongo do Compose (nada é recriado nem apagado)
cd backend
.venv/bin/python loadtest/baseline.py       # tudo (~8 min); --only cap200,failover para parte; --skip-failover
.venv/bin/python loadtest/load.py --base-url http://127.0.0.1:8001 --clients 10 --runs-per-client 5   # só o gerador, contra uma API já no ar
```

O `baseline.py` sobe a API na porta 8001 (e 8002 no teste de falha) com `MODEL_PROVIDER=fake` e uma chave de OpenAI fictícia (as rotas ainda exigem uma chave não vazia, que o modelo simulado nunca usa), derruba tudo e apaga o banco ao final.

## 2. Resultados

### 2.1 Baseline 1, 10 e 50 clientes (rodada 1, `MAX_ACTIVE_RUNS=20`)

Tempos em segundos, `p50 / p95 / p99`.

| Clientes | Tentativas | Concluídas | 429 | Erros | post 202 | 1º evento | 1º delta | término | runs/s | eventos/s |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 15 | 15 | 0 | 0 | 0,018 / 0,034 / 0,034 | 0,54 / 0,56 / 0,56 | 3,09 / 3,26 / 3,26 | 4,63 / 4,80 / 4,80 | 0,21 | 4,7 |
| 10 | 100 | 100 | 0 | 0 | 0,026 / 0,048 / 0,064 | 0,55 / 0,59 / 0,60 | 3,12 / 3,63 / 3,70 | 4,69 / 4,83 / 4,83 | 2,11 | 46 |
| 50 | 300 | 89 | **211 (70%)** | 0 | 0,077 / 0,265 / 0,388 | 0,53 / 0,69 / 0,92 | 3,32 / 3,82 / 3,88 | 4,70 / 5,29 / 5,32 | 3,06 | 67 |

Com o limite padrão de 20 execuções simultâneas por workspace, 50 clientes simultâneos recebem 429 em 70% das tentativas. As 89 execuções aceitas terminaram sem erro e com a mesma latência de quem rodou sozinho (término p50 4,70 s contra 4,63 s): o limite protege as execuções existentes, como a spec exige, e a recusa é barata (post p50 77 ms).

### 2.2 Sem o limite de 20: 50 e 100 clientes (`MAX_ACTIVE_RUNS=200`), duas rodadas

Mesma configuração repetida em duas rodadas, para dar uma ideia da variação entre execuções.

| Clientes | Rodada | Concluídas / tentativas | post 202 | 1º evento | 1º delta | término | runs/s | eventos/s |
|---|---|---|---|---|---|---|---|---|
| 50 | 1 | 300 / 300 | 0,044 / 0,672 / 0,947 | 0,58 / 0,98 / 1,47 | 3,47 / 4,31 / 4,53 | 4,85 / 5,59 / 5,68 | 9,66 | 212 |
| 50 | 2 | 300 / 300 | 0,029 / 0,393 / 0,694 | 0,55 / 0,67 / 1,20 | 3,11 / 3,77 / 3,89 | 4,64 / 4,92 / 5,29 | 10,44 | 230 |
| 100 | 1 | 399 / 400 (1 `ReadError` no cliente) | 0,072 / 4,84 / 5,14 | 0,62 / 5,23 / 5,83 | 3,81 / 8,89 / 9,20 | 5,40 / 10,32 / 10,88 | 14,33 | 315 |
| 100 | 2 | 400 / 400 | 0,044 / 2,88 / 3,60 | 0,59 / 3,44 / 3,70 | 3,67 / 6,07 / 6,78 | 4,89 / 7,51 / 8,28 | 17,25 | 380 |

- Até 50 execuções simultâneas, a cauda sobe pouco (término p95 4,9 a 5,6 s contra 4,8 s de um cliente só). A variação entre as duas rodadas (p95 de 5,59 contra 4,92 s; runs/s de 9,66 contra 10,44) mostra que diferenças menores que cerca de 15% neste ambiente são ruído.
- Com 100 simultâneas, a cauda se deteriora: aceitar a execução (`post 202`) passa a levar de 3 a 5 s no p95, e o término p95 chega a 7,5 a 10,3 s. **O gargalo não foi isolado.** Médias de recursos não indicam saturação óbvia (tabela 2.3): API em ~60% de um núcleo, Mongo em ~150% de 400%, gerador de carga em 22% de um núcleo (medido na rodada 2). Candidatos, todos hipóteses: picos do laço de eventos de um processo Python único, contenção nas transações do Mongo de um nó, disputa de CPU entre API, Mongo e gerador na mesma máquina. Um `ReadError` do cliente em 400 tentativas (rodada 1) não foi investigado; uma hipótese é conexão keep-alive fechada pelo servidor.
- A carga é de laço fechado: quando o sistema desacelera, os clientes oferecem menos. Os runs/s acima são o que a máquina atendeu nessa carga, não um teto.

### 2.3 Recursos

CPU em percentual de **um** núcleo. Mongo é o contêiner (4 núcleos disponíveis).

| Cenário | CPU da API (média) | RSS da API (máx.) | CPU do Mongo (média / pico) | CPU do gerador |
|---|---|---|---|---|
| 1 cliente | 2% | 49 MiB | 14% / 68% | n/m |
| 10 clientes | 9% | 88 MiB | 36% / 174% | n/m |
| 50, limite 20 | 17% | 90 MiB | 45% / 111% | n/m |
| 50, limite 200 (r1 / r2) | 39% / 38% | 95 / 94 MiB | 111% / 83% (pico 173 / 147%) | n/m / 8% |
| 100, limite 200 (r1 / r2) | 57% / 61% | 102 / 101 MiB | 170% / 147% (pico 234 / 240%) | n/m / 22% |
| 50, limite 200, SSE a cada 2 s | 28% | 94 MiB | 96% (pico 161%) | n/m |

(n/m: não medido naquela rodada.) Por execução concluída por segundo, o Mongo consumiu entre 8% e 12% de um núcleo (83/10,44 = 8,0; 111/9,66 = 11,5; 147/17,25 = 8,5; 170/14,33 = 11,9): o custo cresceu aproximadamente de forma linear com o volume, dentro do ruído de cerca de 30% entre rodadas. Extrapolar isso para mais núcleos é hipótese, não medição.

### 2.4 Custo no banco e papel do polling de eventos

Operações do Mongo por execução concluída (contadores `opcounters` do servidor, `serverStatus`, antes e depois de cada cenário):

| Cenário | inserts | queries | updates | commands |
|---|---|---|---|---|
| 1 cliente | 26 | 42,5 | 9 | 72 |
| 10 clientes | 33 | 43 | 13 | 64 |
| 50, limite 200 (r1) | 30 | 45 | 11,5 | 59 |
| 100, limite 200 (r1) | 30 | 45 | 11 | 55 |
| 50, SSE a cada 2 s | 33 | **23** | 13 | 62 |

- O custo por execução é praticamente constante (cerca de 30 inserts, 45 consultas, 11 updates e 55 a 70 comandos), então o volume no Mongo é proporcional às execuções. Cada evento é gravado em uma transação própria (atualiza o contador da execução e insere o evento), o que explica os inserts e comandos.
- **Polling:** cada cliente SSE consulta eventos e estado da execução a cada `SSE_POLL_SECONDS`. Com 0,5 s, a leitura por segundo de stream foi de 7,5 a 11 consultas (50 clientes: 8,5 a 9,2). Subir o intervalo para 2 s derrubou as consultas por execução de 45 para 23 (**-48%**), logo o polling responde por pelo menos metade das leituras com 0,5 s. As outras ~23 leituras por execução não são polling (carregar o grafo publicado, conversa, execução; não isoladas).
- **Mas a CPU do Mongo por execução não caiu de forma distinguível do ruído** ao reduzir as leituras pela metade (11,5% contra 12,2% de um núcleo por run/s; a mesma configuração variou de 8,0 a 11,5% entre rodadas). A CPU da API por run/s caiu de forma modesta (4,0% contra 3,5%). Leitura simples pelo índice `(run_id, seq)` parece barata perto das transações de escrita; o dado é indicativo, não conclusivo.
- **Preço do polling mais raro:** o primeiro evento passou de p50 0,58 s para 2,04 s e o término de 4,85 s para 6,07 s. O 1º evento fica sempre perto de `SSE_POLL_SECONDS` (0,53 a 0,62 s com 0,5 s) porque a primeira consulta depois da conexão costuma chegar antes de a execução gravar `run.started`. Com um cliente, o tempo de término (4,63 s) excede o tempo de modelo simulado (~4,0 s) em ~0,6 s, em grande parte a granularidade do polling.

### 2.5 Falha de instância

Duas instâncias da API no mesmo banco (porta 8001 e 8002). Oito execuções iniciadas na 8001, com o modelo da 8001 deliberadamente lento (300 ms entre trechos) para que sigam ativas. O estado foi lido pela 8002 até cada execução ter pelo menos 100 caracteres de resposta persistidos, e então a 8001 foi morta com `SIGKILL` (sem encerramento gracioso). Lease padrão (30 s), heartbeat de 3 s, manutenção a cada 10 s. Rodada 2 (a rodada 1 teve o mesmo resultado com parcial de apenas 9 caracteres):

| Verificação | Resultado |
|---|---|
| Streams SSE abertos na instância morta | 8 de 8 cortados |
| Estado visto pela outra instância logo após a morte | `running` (o lease ainda vale) |
| Estado final de cada execução, pela outra instância | `interrupted`, erro `interrupted`, evento terminal `run.interrupted` com `code: lease_expired` (8 de 8) |
| Tempo entre a morte e todos os clientes receberem o estado final | 31,4 s (rodada 1: 35,2 s). Esperado: o lease de 30 s mais até 3 s do último heartbeat, mais até 10 s da manutenção |
| Parcial preservado | 8 de 8: 108 a 117 caracteres persistidos antes da morte e o texto final (117 a 126 caracteres) começa com ele |
| Reconexão em outra instância com `after=<último seq>` | Eventos contínuos, sem lacuna nem duplicata (8 de 8); o texto reconstruído pelos eventos SSE é igual ao parcial da execução |
| Nova execução na mesma conversa | 202 e concluída: a conversa não ficou travada pela execução interrompida |

Isso confirma o desenho: sem retomada automática da geração e sem refazer a chamada ao modelo; quem usa envia de novo. O que **não** foi testado: queda do Mongo, partição de rede, queda de uma instância com muitas execuções em carga, e queda durante a gravação do estado final (coberta apenas pelos testes de unidade e integração existentes).

## 3. O que o benchmark não mostra

- **Modelo real.** Com latência de provedor real (segundos a dezenas de segundos), as mesmas execuções duram mais, o número de streams simultâneos por execução/s aumenta, e a resposta final gera mais eventos `response.delta` (um lote a cada 0,25 s de geração: uma resposta de 20 s pode gerar dezenas de eventos, contra ~5 aqui). Isso aumenta escritas e leituras por execução. É hipótese a medir, e o custo em tokens/dinheiro também precisa de rodada real (opcional, com orçamento).
- **Recuperação real.** Embeddings e `$vectorSearch`/busca de texto não foram exercitados. Suas latências e o efeito no Mongo estão fora destes números.
- **Ingestão.** Atraso de fila de ingestão não foi medido.
- **Mongo de produção.** Foi usado um nó único em Docker, sem rede, TLS ou réplicas de verdade. Um cluster gerenciado muda latência, throughput e custo.
- **Várias instâncias sob carga.** A carga rodou em uma instância de API. Duas instâncias só foram usadas no teste de falha.
- **Soak.** Não foi feito teste de longa duração (vazamento de memória, crescimento de coleções, TTL).
- **Mix de rotas.** O mix foi criar conversa, iniciar execução e acompanhar SSE. Listagens, administração e `POST /chat` síncrono não foram medidos.

## 4. Critérios para Redis, fila gerenciada e Kafka

Regra de leitura: cada linha separa **medido** (vem das seções acima), **hipótese** (plausível, não medida) e **gatilho proposto** (um limite objetivo para decidir; os valores numéricos precisam ser calibrados no ambiente real, com modelo real e Mongo de produção, e não são os desta máquina). Preço: não há tabela de custo em dinheiro aqui, porque preço de provedor muda e depende de região e volume. Use a calculadora do provedor com os volumes medidos.

### 4.1 Antes de qualquer componente novo (barato e já disponível)

1. Ajustar `SSE_POLL_SECONDS` conforme a latência de primeiro evento que o produto tolera: cortou as leituras pela metade (medido), ao custo de latência.
2. Escalar o Mongo e medir. As escritas por evento dominam o custo (hipótese coerente com 2.4).
3. Ajustar `MAX_ACTIVE_RUNS` pelo limite do provedor de modelo, não por este benchmark. Neste ambiente, 20 por workspace já recusa 70% de 50 clientes simultâneos e preserva as execuções existentes; sem limite, a cauda piorou entre 50 e 100 simultâneas.
4. Agrupar mais deltas por escrita (`batch_seconds`/`batch_chars` do gravador), se as escritas com modelo real subirem.

### 4.2 Redis (cache, pub/sub, quota distribuída)

| | |
|---|---|
| **Medido** | Nenhuma necessidade hoje: até 50 execuções simultâneas a cauda ficou próxima à de um cliente. O polling é ≥ 48% das leituras com 0,5 s, mas cortá-las pela metade não reduziu a CPU do Mongo por execução de forma distinguível do ruído. Leituras não são o gargalo medido. |
| **Hipótese** | Pub/sub para entregar eventos em tempo real reduziria leituras e a latência do 1º evento (hoje ≈ intervalo de polling), mas o log durável no Mongo continua obrigatório para replay: **as escritas não diminuem**. Cache do snapshot publicado e de conversas poderia cortar parte das ~23 leituras não relacionadas ao polling (não isoladas). Quota atômica no Mongo pode disputar o mesmo documento sob carga (não medido). |
| **Gatilhos propostos** | (a) Com modelo real e o Mongo de produção, no tráfego esperado, a latência p95 de 1º evento ou término excede o objetivo acordado mesmo após o ajuste de 4.1, e o profile mostra leituras de `runs`/`run_events` como parte dominante da CPU ou do I/O do Mongo. (b) Leituras do polling passam de uma fração acordada da capacidade medida do Mongo no pico esperado. (c) Disputa medida (espera de lock/transação) no documento de quota para o limite por usuário ou por workspace. |
| **Custo operacional** | Um serviço a mais com alta disponibilidade (primário e réplica), dimensionamento de memória, escolha de persistência, atualização e monitoramento; segredos e rede (TLS, autenticação); chaves por tenant e invalidação por versão; **fallback obrigatório para o Mongo quando o Redis cair** (senão vira ponto único de falha). Pub/sub não guarda mensagens para quem estava desconectado: o replay continua pelo Mongo. Custo de provedor: nós e réplicas por hora, memória e tráfego. |

### 4.3 Fila gerenciada (SQS, Pub/Sub, Service Bus ou similar)

| | |
|---|---|
| **Medido** | Nada. A fila de ingestão (coleção Mongo com lease) não foi exercitada e o despacho de execuções acontece na própria instância que recebeu a requisição. A falha de instância mostrou que uma execução em andamento vira `interrupted` ao fim do lease, com parcial preservado, sem perder o histórico. |
| **Hipótese** | Seria útil para ingestão (backpressure, atraso e retentativas por job, mais workers) e se for decidido enfileirar execuções acima do limite em vez de responder 429. Para queda de instância não ajuda, já que o desenho **não** refaz a chamada ao modelo automaticamente (evita cobrança e ação duplicadas). |
| **Gatilhos propostos** | (a) Atraso de ingestão (tempo na fila até iniciar) acima do objetivo com os workers disponíveis, ou ingestões concorrentes de vários workspaces competindo; medir com a rodada de ingestão que falta. (b) Decisão de produto por enfileirar execuções (fila de espera) em vez de 429. (c) Necessidade de desacoplar o produtor do consumidor em equipes ou serviços diferentes. |
| **Custo operacional** | Menor que Kafka: sem servidor para operar, mas com cobrança por requisição ou volume, fila de mensagens mortas, tempo de visibilidade (equivalente ao lease), entrega ao menos uma vez: **idempotência e outbox continuam necessárias** (a fila não dá "exatamente uma vez"). Mais um serviço com permissões, alarmes e custo por mensagem. |

### 4.4 Kafka

| | |
|---|---|
| **Medido** | Nenhuma necessidade: no pico testado a API gravou ~230 a 380 eventos/s e o Mongo de um nó em Docker manteve ~460 inserts/s e ~840 comandos/s, com cauda degradada mas sem erro. Isso não diz onde está o limite de um Mongo de produção. |
| **Hipótese** | Faria sentido se vários consumidores independentes precisarem dos eventos com retenção e reprocessamento (auditoria, analytics, avaliação), ou se o volume de eventos ultrapassar o que o Mongo sustenta com folga. Particionar por `run_id` preservaria a ordem por execução. |
| **Gatilhos propostos** | (a) Um segundo consumidor real dos eventos que precise de replay além da retenção do Mongo (TTL de 7 dias por padrão). (b) Taxa sustentada de eventos no pico esperado que, no ambiente real, consuma uma fração do Mongo acima do limite acordado após escalar o banco e comparar com a fila gerenciada. (c) Requisito de retenção/replay de longo prazo. Comparar sempre com a fila gerenciada antes. |
| **Custo operacional** | O mais alto: cluster com vários brokers, partições, retenção e armazenamento, controle de acesso, atualização, monitoramento de lag e rebalanceamento, e competência da equipe (ou piso de preço de um serviço gerenciado). Também não dá "exatamente uma vez" fim a fim: idempotência e outbox continuam necessárias. |

### 4.5 O que medir para decidir de verdade

1. Repetir 1/10/50/100 com modelo real (rodada opcional, com orçamento) e com o Mongo de produção: latência, taxa de eventos por execução (os deltas reais), custo e tokens por execução.
2. Isolar o gargalo dos 100 simultâneos (perfil da API e do Mongo: tempo em transações, filas de escrita, laço de eventos).
3. Medir atraso de ingestão com a fila atual.
4. Rodar um soak de horas, e duas ou mais instâncias de API sob carga.
5. Definir os objetivos (p95 de 1º evento e de término, taxa de erro, disponibilidade) antes de aplicar os gatilhos acima: sem objetivo, os gatilhos não têm referência.
