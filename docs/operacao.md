# Operação

Rotina, backup e restauração, rollback e migrações do ambiente Docker Compose desta solução. Os comandos rodam na raiz do repositório. Marcação usada abaixo: **verificado** significa que o comando foi executado em 03/11/2026 neste ambiente; **não executado** significa que o procedimento segue o código, mas não foi rodado por ser destrutivo.

Regra geral: nunca use `docker compose down -v`, `docker volume rm` nem apague os volumes `mongo-data`, `mongo-config`, `mongo-search` como forma de "consertar". Eles guardam o banco, e os índices de busca.

## Serviços e portas

| Serviço | Imagem / origem | Porta no host | Função |
|---|---|---|---|
| `mongo` | `mongodb/mongodb-atlas-local:8.0.4` | `127.0.0.1:27017` | MongoDB com Atlas Search e Vector Search, replica set de um nó |
| `api` | `backend/Dockerfile` | `127.0.0.1:8000` | API FastAPI |
| `worker` | mesma imagem da API | nenhuma | Fila de ingestão |
| `frontend` | `frontend/Dockerfile` (nginx) | `127.0.0.1:8080` | Interface; encaminha `/api` à API sem buffer para SSE |

Todas as portas ficam presas a `127.0.0.1`: o ambiente é de demonstração e não expõe a API à rede.

```bash
docker compose up -d --build        # sobe ou atualiza
docker compose ps                   # saúde
docker compose logs -f api worker   # logs
curl -s localhost:8000/api/v1/health/live    # {"status":"ok"} (liveness)
curl -s localhost:8000/api/v1/health/ready   # {"status":"ok"} se o Mongo responde; 503 caso contrário
```

Verificado: `api` e `mongo` aparecem `healthy`, `GET /health/live` e `GET /health/ready` respondem `{"status":"ok"}` e `docker compose config -q` valida o arquivo. O healthcheck da `api` usa `/health/live`: a liveness não depende de banco.

## Índices de busca

Índices comuns (conversas, agentes, conhecimento, fila, dados de suporte) e o TTL dos eventos são criados na inicialização da API e do worker, de forma idempotente. Os índices de busca (`knowledge_text` e `knowledge_vector`) são criados pelo worker ao iniciar e pelos comandos abaixo, que também esperam a construção terminar (o Atlas Local os constrói de forma assíncrona).

`apply_indexes` é o passo de implantação: aplica **todos** os índices de que a aplicação precisa (conversas, execuções e eventos com o TTL, agentes e grafo, conhecimento, fila, busca textual e vetorial) e a retenção opcional do histórico (`HISTORY_RETENTION_DAYS`), sem nunca apagar dados. Rode-o antes de subir uma versão nova e depois de qualquer restauração:

```bash
docker compose exec api python -m app.adapters.inbound.cli.apply_indexes --wait 90
# run_events_ttl: 7d
# conversations_retention_ttl: off   (e messages, runs, run_evidence)
# knowledge_text: exists
# knowledge_vector: exists
# índices prontos para consulta.
```

`ensure_indexes` é a versão menor, só da base de conhecimento (índices comuns e de busca):

```bash
docker compose exec api python -m app.adapters.inbound.cli.ensure_indexes --wait 90
# knowledge_text: exists / knowledge_vector: exists / índices prontos para consulta.
```

Verificado (os dois comandos, código de saída 0). Se o prazo passar sem que os índices fiquem consultáveis, o comando termina com código 1 (`índices ainda em construção`). Localmente, sem Docker para a API: `cd backend && PYTHONPATH=src .venv/bin/python -m app.adapters.inbound.cli.apply_indexes --wait 90`.

`HISTORY_RETENTION_DAYS` (ausente por padrão: o histórico é mantido) é declarativo: com um valor, `apply_indexes` cria índices TTL em conversas, mensagens, execuções e evidências com essa idade; sem ele, remove esses índices (e nada mais é apagado dali em diante). A retenção dos eventos de execução é separada (`EVENT_RETENTION_DAYS`, 7 dias).

Trocar `EMBEDDING_MODEL` para um modelo com outra dimensão faz o comando recusar a execução (`O índice knowledge_vector usa N dimensões…`): remova o índice vetorial, reprocesse a base e recrie-o; chunks de modelos diferentes nunca são misturados na busca.

## Backup e restauração

`infra/mongo/backup.sh` gera um arquivo `mongodump --archive --gzip` do banco `getnet_support` através do serviço `mongo`. `infra/mongo/restore.sh` restaura num banco **separado** de verificação, sem tocar o banco em uso.

```bash
sh infra/mongo/backup.sh
# backups/getnet_support-20261104T021918Z.archive.gz

sh infra/mongo/restore.sh backups/getnet_support-20261104T021918Z.archive.gz
# agent_versions: 6 / agents: 3 / audit_events: 6 / conversations: 12 / graph_versions: 4 / ingestion_jobs: 1
# knowledge_chunks: 146 / knowledge_sources: 20 / messages: 24 / prompt_versions: 6 / receivables: 1
# run_events: 270 / run_evidence: 12 / runs: 12 / terminals: 1 / workspaces: 1
```

Variáveis do shell (não do `.env`): `MONGO_DATABASE` (padrão `getnet_support`), `BACKUP_DIR` (padrão `backups`, que está no `.gitignore`) e `TARGET_DB` (padrão `<banco>_restore_check`).

**Verificado** (03/11/2026): backup de 1,1 MB; restauração no banco `getnet_support_restore_check` com os mesmos 16 conjuntos e contagens do banco em uso. O script restaura com `--drop` apenas nas coleções do banco de destino (`--nsFrom`/`--nsTo`), então o banco em uso não é afetado. Depois de conferir, remova o banco de verificação:

```bash
docker compose exec -T mongo mongosh --quiet --eval "db.getSiblingDB('getnet_support_restore_check').dropDatabase()"
```

**Os índices de busca não fazem parte do dump.** Verificado: o banco restaurado tinha 0 índices de busca. Depois de qualquer restauração para o banco em uso, recrie-os e espere ficarem consultáveis com `apply_indexes --wait 90` (ou `ensure_indexes --wait 90`), acima. Os índices comuns e o TTL voltam sozinhos com o dump e a inicialização dos serviços.

**Recuperação real** (não executado, pois substitui dados): restaure sobre o banco em uso apenas se o banco estiver perdido ou corrompido.

```bash
docker compose stop api worker                       # nenhuma escrita durante a recuperação
TARGET_DB=getnet_support sh infra/mongo/restore.sh backups/<arquivo>.archive.gz
docker compose start api worker
docker compose exec api python -m app.adapters.inbound.cli.apply_indexes --wait 90
curl -s localhost:8000/api/v1/health/ready
```

`TARGET_DB=getnet_support` com `--drop` substitui as coleções do banco em uso pelas do arquivo. Antes, faça um backup novo do estado atual. Execuções que estavam ativas no momento do backup voltam como `running` com o lease já vencido: a varredura de leases (a cada 10 s) as marca `interrupted`.

Limites a conhecer:

- `mongodump --db` não é um instantâneo consistente entre coleções com a aplicação escrevendo. Para uma cópia coerente, faça o backup com a API ociosa; a restauração de teste acima serve para conferir o arquivo, não para garantir ponto no tempo.
- O arquivo contém conversas, textos de resposta, eventos e embeddings. Trate-o como dado sensível mesmo com os dados de suporte sendo fictícios. Nenhum segredo está no banco (a chave da OpenAI vem de `.env`).
- Backup em produção, retenção e criptografia dependem do deployment Mongo escolhido; veja [producao.md](producao.md).

## Rollback por imagem

A aplicação (API, worker, frontend) é imagem sem estado: o rollback é voltar à imagem anterior, sem apagar volumes. O Compose constrói as imagens localmente e as nomeia `<projeto>-<serviço>` (o projeto é o nome do diretório; aqui `desafio-api`, `desafio-worker`, `desafio-frontend`). Antes de atualizar, guarde a imagem atual:

```bash
docker tag desafio-api:latest desafio-api:previous
docker tag desafio-worker:latest desafio-worker:previous
docker tag desafio-frontend:latest desafio-frontend:previous
docker compose up -d --build api worker frontend     # atualização
```

Para voltar atrás:

```bash
docker tag desafio-api:previous desafio-api:latest
docker tag desafio-worker:previous desafio-worker:latest
docker tag desafio-frontend:previous desafio-frontend:latest
docker compose up -d --no-build api worker frontend
```

Alternativa sem tags: `git checkout <commit-anterior>` e `docker compose up -d --build api worker frontend`.

Verificado: `docker tag` e `docker rmi` da etiqueta funcionam e `--no-build` existe em `docker compose up`. A troca de containers em si não foi executada (derrubaria o ambiente em uso). O banco não precisa voltar junto: o esquema é aditivo (próxima seção) e as versões de agentes, prompts e grafo são imutáveis, então o código antigo continua lendo os dados novos. Só se uma migração futura não for compatível com o código anterior, restaure um backup feito antes dela (seção anterior). Execuções em andamento são interrompidas na troca (`interrupted`, com o texto parcial salvo); o usuário reenvia a mensagem.

## Migrações aditivas

O esquema evolui só por adição: campos novos opcionais, índices novos e leitores tolerantes (`.get`), de modo que a versão N e a N-1 da aplicação convivam com o mesmo banco. O que o código já faz sozinho, de forma idempotente, a cada inicialização:

| Mecanismo | Onde | Efeito |
|---|---|---|
| Índices comuns e TTL | `MongoConversations.ensure_indexes`, `MongoAgentRepository.ensure_indexes`, `MongoHandoffRepository.ensure_indexes`, `MongoKnowledgeRepository.ensure_indexes`, `MongoIngestionJobs.ensure_indexes` | `create_index` não faz nada se o índice existe. Mudar `EVENT_RETENTION_DAYS` atualiza o TTL no lugar (`collMod`). |
| Índices de busca | `ensure_search_indexes` (worker e CLIs `ensure_indexes` e `apply_indexes`) | Cria os que faltam; recusa dimensão vetorial divergente. |
| Passo de implantação | CLI `apply_indexes` | Aplica todos os índices e a retenção do histórico; aditivo, nunca apaga dados. |
| Seed | `seed_demo` | Insere agentes ausentes; o grafo inicial só é publicado na primeira vez (`graph_version == 0`), então reexecuções nunca alteram o que o administrador editou ou publicou. Os dados de demonstração são regravados a cada início. |
| Reindexação | `index_version` na ingestão | Mudou conteúdo, chunking, modelo ou metadados editoriais: só essa fonte é reprocessada; as demais ficam `already_current`. |

Há um caminho **não aditivo** e ele é deliberado: ao encontrar o layout antigo da base de conhecimento (um documento por fonte, com índice único `workspace_id_1_source_id_1`), `MongoKnowledgeRepository.ensure_indexes` remove esse índice e apaga os chunks sem `chunk_id`; é preciso reingerir a base depois. Não se aplica a bancos criados por esta versão.

Para introduzir uma mudança de esquema:

1. Adicione o campo ou índice como opcional e faça o código tolerar a ausência. Evite renomear ou remover campos na mesma versão.
2. Crie os índices antes do código que os usa: rode `apply_indexes --wait 90` como passo de implantação, antes de subir a versão nova.
3. Se for preciso preencher dados antigos, faça-o como script idempotente e reexecutável, e rode-o depois de subir a versão que escreve o campo novo.
4. Faça backup, aplique, confira com `health/ready` e uma consulta real. Não use recriar volume ou apagar coleção como rollback.
5. Remover um campo é uma segunda versão, publicada só quando nenhuma imagem anterior que o leia puder ser restaurada.

## Diagnóstico rápido

| Sintoma | Causa provável | Ação |
|---|---|---|
| `POST /chat` responde `503 provider_not_configured` | `OPENAI_API_KEY` vazia no `.env` | Preencha a chave e recrie `api` e `worker`: `docker compose up -d api worker` (o `.env` só é lido na criação do container) |
| `POST /knowledge/ingestions` responde `409 no_sources` | O worker ainda não registrou o catálogo | `docker compose logs worker`; confirme que está de pé |
| Respostas sem trechos / "evidência insuficiente" | Base sem ingestão ou índices de busca ausentes | `GET /knowledge/sources` (todos `indexed`?); crie a ingestão; rode `apply_indexes --wait 90` (obrigatório depois de um restore) |
| `GET /health/ready` responde 503 | Mongo indisponível | `docker compose ps mongo`, `docker compose logs mongo` |
| Execução `interrupted` | A instância que a executava parou ou perdeu o lease | Reenvie a mensagem; investigue `docker compose logs api` |
| `429 capacity_exceeded` | Execuções ativas do workspace no limite | Aguarde (`Retry-After: 5`) ou ajuste `MAX_ACTIVE_RUNS` |
| Fonte oficial em `failed` com código `empty` | O HTML entregue não tem texto útil (conteúdo via JavaScript ou bloco principal errado) | Rode `extract_markdown` no HTML baixado para ver o que sai; a versão indexada anterior continua valendo |

Para investigar uma execução, use o `trace_id` (de `POST /chat` ou `GET /runs/{id}`) ou o `request_id` dos erros e do cabeçalho `X-Request-ID`.
