# Preparação para produção

Este documento descreve o que existe no código para operar o atendimento multiagente com várias pessoas usuárias e o que a infraestrutura precisa fornecer. Onde algo ainda não existe, está dito como lacuna. Nenhum número aqui é meta de conformidade ou de capacidade: os números de carga estão em [capacidade.md](capacidade.md) e são cenários de teste.

## 1. Perfis

| Perfil (`APP_PROFILE`) | Identidade | Quando usar |
|---|---|---|
| `local-demo` (padrão) | Usuário e workspace fixos (`DEMO_USER_ID`, `DEMO_WORKSPACE_ID`), papel `admin`, marcados como demonstração. Nenhum token é exigido. | Desenvolvimento, CI, demonstração. Semeia agentes e dados fictícios. |
| `production` | Token Bearer OIDC verificado no servidor. Sem fallback para a identidade demo. | Qualquer implantação com mais de uma pessoa usuária. |

O perfil `production` recusa iniciar (erro de validação das configurações, em pt-BR) quando:

- falta `OIDC_ISSUER` ou `OIDC_AUDIENCE`;
- `OIDC_ISSUER` ou `OIDC_JWKS_URI` não usam `https://`;
- `OIDC_ALGORITHMS` contém algo que não seja assimétrico (`HS*` e `none` são rejeitados);
- `MODEL_PROVIDER=fake` (modelo simulado de testes de carga, nunca para produção).

O perfil de produção também não semeia nada (`seed_demo` só roda em `local-demo`). Há duas consequências operacionais:

1. O grafo de agentes inicial precisa ser criado pela API administrativa (criar `router`, `support`, `knowledge`, vínculos e publicar) ou por um job controlado. Sem grafo publicado, as execuções falham com o código `no_router`.
2. As ferramentas de suporte (recebíveis e terminais) leem coleções do próprio Mongo por `(workspace_id, user_id)`. Não há integração com sistemas reais da Getnet; em produção essas consultas devolvem "sem registros" para quem não tiver dados carregados. Integrar sistemas reais é trabalho futuro.

## 2. OIDC

A API valida JWTs Bearer emitidos por um provedor OIDC externo (Keycloak, Entra ID, Auth0, Cognito etc.). Ela não emite tokens nem faz login: o frontend ou o cliente obtém o token no provedor (authorization code com PKCE) e o envia em `Authorization: Bearer <token>`.

### Variáveis

| Variável | Padrão | Função |
|---|---|---|
| `OIDC_ISSUER` | obrigatória | Valor exato do `iss`. A descoberta é feita em `{issuer}/.well-known/openid-configuration`, e o `issuer` do documento precisa ser igual ao configurado. |
| `OIDC_AUDIENCE` | obrigatória | Valor exigido em `aud`. |
| `OIDC_JWKS_URI` | descoberto | Fixa a URL do JWKS e dispensa a descoberta. Precisa ser `https://`. |
| `OIDC_ALGORITHMS` | `RS256` | Lista separada por vírgula, só `RS*`, `PS*`, `ES*`. |
| `OIDC_WORKSPACE_CLAIM` | `workspace_id` | Claim com o workspace da pessoa. Aceita nome literal (inclusive URL) ou caminho com pontos. |
| `OIDC_ALLOWED_WORKSPACES` | vazio | Lista separada por vírgula. Se definida, o workspace do token precisa estar nela. |
| `OIDC_ROLES_CLAIM` | `roles` | Claim com os papéis (lista ou texto). Aceita caminho com pontos, por exemplo `realm_access.roles`. |
| `OIDC_ADMIN_ROLE` / `OIDC_OPERATOR_ROLE` | `admin` / `operator` | Nomes dos papéis no provedor. |
| `OIDC_LEEWAY_SECONDS` | `30` | Tolerância para diferença de relógio em `exp`, `nbf` e `iat`. |
| `OIDC_JWKS_TTL_SECONDS` | `600` | Validade do cache das chaves. |
| `OIDC_JWKS_MIN_REFRESH_SECONDS` | `30` | Intervalo mínimo entre buscas do JWKS provocadas por `kid` desconhecido. |

### O que é verificado

1. Cabeçalho: `alg` precisa estar em `OIDC_ALGORITHMS` (portanto `none` e `HS*` nunca passam, o que também impede o ataque de confusão de algoritmo) e `kid` precisa ser texto.
2. Assinatura com a chave do JWKS do emissor. O cache vale `OIDC_JWKS_TTL_SECONDS`. Um `kid` desconhecido (rotação de chave) força nova busca, no máximo uma por `OIDC_JWKS_MIN_REFRESH_SECONDS`, para que tokens com `kid` aleatório não sobrecarreguem o provedor. Chaves com `use: enc` são ignoradas.
3. `iss`, `aud`, `exp` obrigatórios; `nbf` e `iat` quando presentes, com a tolerância configurada; `sub` obrigatório, texto de 1 a 200 caracteres.
4. Provedor fora do ar: com cache aquecido, chaves já conhecidas continuam válidas (a rotação só é percebida quando o provedor volta). Sem nenhuma chave em cache, a API responde 503 `service_unavailable` com `Retry-After`.

### Mapeamento para identidade

| Identidade | Origem |
|---|---|
| `user_id` | `sub` do token. |
| `workspace_id` | Claim configurada. Precisa casar com `[A-Za-z0-9._:-]{1,64}` e, se houver lista, estar em `OIDC_ALLOWED_WORKSPACES`. O provedor é a autoridade sobre a associação pessoa e workspace: a lista é a trava adicional recomendada. |
| `role` | `admin` se o papel de administrador estiver nos papéis do token; senão `operator` se o papel de operador estiver; senão acesso negado. |

O `user_id` enviado no corpo (`POST /chat`, `POST /conversations/{id}/runs`) nunca é confiável: precisa ser igual ao `sub` do token, ou a resposta é 403. Todas as consultas privadas (conversas, mensagens, execuções, eventos, evidências) são filtradas por workspace e usuário do token. Conversa ou execução de outra pessoa responde 404, sem revelar o conteúdo. Um administrador não lê conversas de outras pessoas.

### Respostas de erro

| Situação | Resposta |
|---|---|
| Token ausente, mal formado, assinatura inválida, `iss`/`aud` errados, algoritmo não permitido | 401 `unauthorized`, `WWW-Authenticate: Bearer error="invalid_token"` (sem `error` quando nenhum token foi enviado). |
| Token expirado | 401 `unauthorized`, mensagem "Sessão expirada". |
| Token válido sem workspace autorizado ou sem papel | 403 `forbidden`. |
| Operador em rota administrativa, ou `user_id` do corpo diferente do `sub` | 403 `forbidden`. |
| Provedor de identidade inacessível e sem chaves em cache | 503 `service_unavailable`, `Retry-After: 5`. |

O corpo segue o padrão da API: `{code, message, request_id, details?}`. As mensagens não dizem qual verificação falhou.

Limite conhecido: o token é validado quando a requisição começa. Um stream SSE em andamento não é derrubado se o token expirar durante ele; como a execução tem limite de tempo (`RUN_TIMEOUT_SECONDS`, 120 s por padrão), o stream não dura além disso. Não há revogação instantânea: o efeito de revogar uma pessoa no provedor chega com a expiração do token (use tokens de vida curta).

## 3. RBAC

| Papel | Pode |
|---|---|
| `operator` | Conversar (`/chat`, `/conversations`, `/runs`), ver e cancelar as próprias execuções, ler as próprias evidências. |
| `admin` | Tudo do operador, mais `/agents`, `/prompts`, `/graph` e `/knowledge` (configurar, publicar, restaurar versões, ingerir base de conhecimento). |

O papel vem sempre do token, e é aplicado no servidor (`require_admin`). Testes: `backend/tests/test_prod_oidc.py` (toda rota protegida responde 401 sem token válido; toda rota administrativa responde 403 para operador) e `backend/tests/test_prod_isolation.py` (isolamento entre usuários e entre workspaces, com Mongo).

## 4. TLS

TLS termina na infraestrutura (load balancer, ingress ou proxy reverso). O processo da API escuta HTTP em rede privada e não implementa TLS.

- Exponha apenas HTTPS. Redirecione HTTP e habilite HSTS no proxy.
- SSE: o proxy não pode armazenar a resposta em buffer (a API envia `X-Accel-Buffering: no`) e o tempo ocioso do balanceador precisa ser maior que o heartbeat SSE (`SSE_HEARTBEAT_SECONDS`, 15 s).
- A API fala com o provedor OIDC apenas por `https://` (validado na inicialização).
- Mongo: use TLS e autenticação na `MONGO_URI` (`tls=true`, usuário e senha ou autenticação por certificado). O Compose local não usa nenhum dos dois e é só para desenvolvimento.
- Telemetria: o endpoint OTLP precisa estar em rede confiável ou usar TLS.

## 5. Segredos

- `OPENAI_API_KEY`, credenciais do Mongo e qualquer outro segredo vêm de um gerenciador de segredos ou das variáveis do orquestrador. Nunca na imagem, no repositório ou em `compose.yaml`. O `.env` está no `.gitignore`.
- Os segredos são `SecretStr` nas configurações: não aparecem em `repr` ou logs de configuração. Telemetria e eventos SSE não carregam texto de mensagens, argumentos sensíveis, tokens ou raciocínio do modelo.
- Nenhum segredo de OIDC é necessário na API: ela só valida tokens com chaves públicas. Não coloque tokens em URLs nem em logs do proxy.
- Rotacione a chave da OpenAI e as credenciais do Mongo por reimplantação; a API lê as variáveis uma vez na inicialização.

## 6. Quotas e limites

| Limite | Configuração | Comportamento |
|---|---|---|
| Execuções simultâneas por workspace | `MAX_ACTIVE_RUNS` (20) | Acima disso, 429 `capacity_exceeded` com `Retry-After: 5`. As execuções em andamento seguem. |
| Profundidade, delegações, chamadas de ferramenta, tempo e tokens de saída por execução | `RUN_MAX_DEPTH`, `RUN_MAX_DELEGATIONS`, `RUN_MAX_TOOL_CALLS`, `RUN_TIMEOUT_SECONDS`, `RUN_MAX_OUTPUT_TOKENS` | A execução termina com erro estável (`limit_*`). |
| Uma execução ativa por conversa | fixo | 409 `conversation_busy`. |
| Idempotência | `Idempotency-Key` | Mesma chave e corpo devolve a mesma execução; corpo diferente, 409. |

Lacunas assumidas:

- O limite `MAX_ACTIVE_RUNS` conta e depois insere (não é atômico): uma rajada simultânea pode ultrapassá-lo um pouco. Se isso importar, use um documento de quota no Mongo com incremento atômico.
- É um limite por workspace, não por usuário, e não há limite de taxa (requisições por segundo) nem orçamento em dinheiro por workspace. Os limites do provedor de modelo (OpenAI) valem por fora e a API não os conhece.
- Cada instância aplica `MAX_ACTIVE_RUNS` lendo o Mongo, então o limite é global entre instâncias, com a ressalva acima.

## 7. Retenção

| Dado | Configuração | Padrão |
|---|---|---|
| Eventos de execução (`run_events`) | `EVENT_RETENTION_DAYS` | 7 dias. Cursor SSE fora da retenção responde 410. A execução e o histórico seguem consultáveis. |
| Conversas, mensagens, execuções e evidências | `HISTORY_RETENTION_DAYS` | **Desligada** (mantém para sempre). Ao definir, cria índices TTL nas quatro coleções. |

Os dois valores são aplicados como índices TTL pelo comando de índices (seção 8). A remoção é feita pelo próprio Mongo, em segundo plano, e é irreversível. Desligar `HISTORY_RETENTION_DAYS` e rodar o comando remove os índices TTL (nada é apagado a partir daí). Quem define o número de dias é a organização, conforme a sua política e a legislação aplicável; este repositório não inventa prazo de conformidade.

Atenção: backups guardam o que o TTL já apagou. Alinhe a retenção dos backups com a política, senão a exclusão não acontece de fato.

Lacuna: não existe endpoint de exclusão de dados de uma pessoa específica (pedido de titular). Até existir, a exclusão é um procedimento operacional no Mongo por `workspace_id` e `user_id`.

## 8. Índices e migrações

O esquema é aditivo: coleções e índices novos nunca removem dados, e as versões publicadas de agentes e prompts são imutáveis. Isso permite voltar o código para a imagem anterior sem desfazer o banco.

Comando idempotente, a executar como etapa de implantação antes de subir a nova versão (pode rodar quantas vezes quiser, e em paralelo com instâncias já ativas):

```sh
# a partir de backend/, com MONGO_URI e MONGO_DATABASE do ambiente
PYTHONPATH=src python -m app.adapters.inbound.cli.apply_indexes --wait 300

# com o Compose
docker compose run --rm api python -m app.adapters.inbound.cli.apply_indexes --wait 300
```

Ele aplica: índices de conversas, mensagens, execuções, eventos (com TTL) e evidências; agentes, versões de agente, versões de prompt, versões de grafo e auditoria; fontes e trechos de conhecimento; trabalhos de ingestão; os índices Atlas Search (`knowledge_text`) e Vector Search (`knowledge_vector`); e a retenção de histórico, conforme a configuração. Cada linha impressa mostra o estado; com `--wait` o comando aguarda os índices de busca ficarem consultáveis e sai com código 1 se não ficarem no prazo.

Cuidados:

- Trocar `EMBEDDING_MODEL` por outro com dimensão diferente faz o comando falhar de propósito (o índice vetorial existente usa outra dimensão). É preciso remover o índice vetorial e reprocessar a base; o comando nunca mistura dimensões em silêncio.
- A primeira execução sobre um banco com o layout antigo de `knowledge_chunks` (índice único legado) remove esse índice e apaga os trechos antigos sem `chunk_id`. A base é reconstruída reingerindo. Faça backup antes dessa migração específica.
- O comando do worker de ingestão também prepara os índices de conhecimento ao subir, mas não os de conversas e agentes. A API em `production` cria só os índices de conversas e eventos na inicialização. Use o comando completo como etapa de implantação.

Testes: `backend/tests/test_prod_indexes.py` (idempotência, TTL de eventos alterado no lugar, retenção de histórico ligada, alterada e desligada).

## 9. Backup e restauração

Em produção, use o backup do serviço Mongo escolhido (por exemplo, snapshots contínuos com restauração a um ponto no tempo no Atlas), com política de retenção definida pela organização. RPO e RTO não estão definidos aqui: precisam ser acordados e testados.

Para desenvolvimento e verificação, o repositório traz dois scripts que usam o serviço `mongo` do Compose:

```sh
infra/mongo/backup.sh                      # gera backups/<banco>-<UTC>.archive.gz (mongodump, gzip)
infra/mongo/restore.sh backups/<arquivo>   # restaura em <banco>_restore_check e imprime a contagem por coleção
TARGET_DB=getnet_support infra/mongo/restore.sh <arquivo>   # recuperação real: --drop substitui as coleções do destino
```

- O dump não inclui índices Atlas Search nem Vector. Depois de restaurar, rode o comando da seção 8.
- A restauração padrão vai para um banco separado de verificação: use-a periodicamente para provar que o backup restaura. Só aponte `TARGET_DB` para o banco vivo numa recuperação de fato.
- Backups contêm dados pessoais (mensagens). Cifre em repouso, restrinja o acesso e aplique retenção (seção 7).
- Rollback da aplicação: imagem anterior. Se uma migração de dados não for compatível, restaure um backup testado; não recrie volumes nem apague dados como rollback padrão.

## 10. Checklist de go-live

Identidade e acesso

- [ ] Provedor OIDC definido; `OIDC_ISSUER` e `OIDC_AUDIENCE` configurados; emissor em HTTPS.
- [ ] Token de teste de cada papel emite as claims esperadas (`sub`, workspace, papéis); `OIDC_*_CLAIM` e nomes de papel conferidos.
- [ ] `OIDC_ALLOWED_WORKSPACES` definida (ou decisão registrada de confiar em qualquer workspace do emissor).
- [ ] Tokens de vida curta; fluxo de login com PKCE no frontend.
- [ ] Sem `APP_PROFILE=local-demo` e sem `MODEL_PROVIDER=fake` no ambiente (a API já recusa iniciar assim em produção).
- [ ] Operador recebe 403 nas rotas administrativas e 401 sem token (testes `test_prod_*` verdes, mais verificação manual no ambiente).

Rede e segredos

- [ ] TLS no balanceador ou ingress, HSTS, HTTP redirecionado; sem buffering de resposta e sem timeout ocioso menor que o heartbeat SSE.
- [ ] Mongo com TLS, autenticação e acesso restrito à rede da API; compatível com Search e Vector Search, replica set (as execuções usam transações).
- [ ] `OPENAI_API_KEY` e credenciais do Mongo em gerenciador de segredos; nada no repositório, imagem ou logs.
- [ ] Limites e orçamento da conta OpenAI configurados no provedor.

Dados

- [ ] Comando de índices executado e `--wait` concluído com sucesso.
- [ ] Grafo de agentes inicial criado e publicado (`router`, `support`, `knowledge`); uma execução de teste ponta a ponta concluída.
- [ ] Base de conhecimento ingerida; busca híbrida respondendo.
- [ ] `EVENT_RETENTION_DAYS` e `HISTORY_RETENTION_DAYS` decididos pela organização e aplicados.
- [ ] Backup automático ativo; restauração testada em banco separado; índices recriados depois do teste.

Operação

- [ ] Duas ou mais instâncias da API atrás do balanceador, sem afinidade de sessão (estado e eventos estão no Mongo); readiness em `/api/v1/health/ready`, liveness em `/api/v1/health/live`.
- [ ] `MAX_ACTIVE_RUNS` e limites de execução revisados com base em [capacidade.md](capacidade.md), medindo de novo no ambiente real.
- [ ] Logs acessíveis só a quem deve e sem conteúdo de mensagens. Aponte `OTEL_EXPORTER_OTLP_ENDPOINT` para o coletor OpenTelemetry da plataforma (o `observability` do Compose é só para desenvolvimento) e restrinja o acesso ao Grafana.
- [ ] Procedimento de rollback (imagem anterior) e de restauração conhecido por quem opera.
- [ ] Ciência das lacunas: sem limite por usuário, sem orçamento em dinheiro, sem exclusão por titular, sem revogação instantânea de token, ferramentas de suporte simuladas.
