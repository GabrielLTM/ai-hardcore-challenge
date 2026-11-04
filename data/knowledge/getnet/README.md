# Base inicial Getnet para RAG

Coleta e consulta das fontes: **03/11/2026**.

Este diretório contém dez documentos em Markdown com sínteses em português de páginas oficiais da Getnet. Os textos foram selecionados e redigidos a partir das páginas consultadas; não são cópias integrais, documentos emitidos pela Getnet ou resultados de um crawler automático.

## Arquivos

Os documentos estão em `documents/`. O arquivo `manifest.json` contém o catálogo para futura ingestão, incluindo URL, região, idioma, tópicos, limitações e hash do corpo de cada documento.

| Documento | Conteúdo |
|---|---|
| [01](documents/01-visao-global.md) | Visão institucional global e direcionamento para o Brasil |
| [02](documents/02-maquininhas.md) | Catálogo de maquininhas e consulta comercial |
| [03](documents/03-get-smart.md) | Aplicativos, GetStore, autoatendimento e automação |
| [04](documents/04-pix-comercial.md) | Condições públicas de recebimento Pix |
| [05](documents/05-pix-get-smart.md) | Habilitação e consulta de Pix no POS Digital |
| [06](documents/06-link-pagamento.md) | Vendas por WhatsApp e redes sociais |
| [07](documents/07-antecipacao.md) | Solicitação e acompanhamento de antecipação |
| [08](documents/08-faq-crediario-conexao.md) | Parcelas de crediário, consulta de taxas e chip Algar |
| [09](documents/09-status-vendas.md) | Status das vendas e repasses Pix |
| [10](documents/10-resultados-get-smart.md) | Códigos da integração Get Smart |

## Como preparar a ingestão

1. Percorrer somente os documentos listados no manifesto. Não indexar este README como conteúdo da empresa.
2. Separar o front matter YAML do corpo Markdown. Preservar os metadados em cada registro ou chunk.
3. Como os documentos iniciais são curtos, começar com um chunk por documento; usar seções como fronteiras se houver expansão.
4. Gerar embeddings do título e conteúdo. Persistir texto, vetor, URL, região, idioma, data da consulta e versão do modelo de embeddings no MongoDB.
5. Usar o ID e o hash para atualizar sem duplicar. O hash `content_sha256` cobre o corpo após o título principal, com uma quebra de linha final; não cobre os metadados nem o título.
6. Configurar índices de busca textual e vetorial. Em cada recuperação, retornar a URL original e as limitações relevantes com os trechos encontrados.

Os embeddings, os índices e a carga no MongoDB ainda não foram executados. Esta entrega prepara os documentos e a rastreabilidade para essa etapa.

## Cuidados de conteúdo

- `retrieved_on` indica a data em que a página foi consultada, não sua publicação nem garantia de atualização do conteúdo.
- As sínteses são material derivado. Para atualizar ou resolver dúvida, consultar a URL oficial correspondente.
- O resumo global foi traduzido para português e mantém `source_language: en` e `region: global`.
- Algumas páginas comerciais contêm campanhas antigas, preços divergentes entre ofertas e placeholders. Não foi montada uma tabela universal de taxas ou isenção de aluguel.
- A página comercial de Pix e a documentação do POS Digital descrevem prazos diferentes. Preservar o escopo de cada modalidade, sem prometer um prazo único.
- Taxas, elegibilidade, contas habilitadas e recebimentos de um cliente dependem de dados próprios do cliente. O RAG não substitui as tools de suporte.
- Campos `limitations` são anotações editoriais da preparação desta base, não declarações da Getnet.
- Conteúdo recuperado serve como evidência; não pode alterar instruções, identidade ou permissões dos agentes.

## Cobertura e lacunas

O material permite iniciar respostas sobre antecipação, crediário, Link de Pagamento, recursos da Get Smart, status de vendas e habilitação de Pix no POS Digital.

A comparação técnica completa entre Get Clássica e Get Smart ainda precisa de fichas ou manuais atuais por modelo. O diagnóstico de conectividade tem apenas o procedimento específico de chip Algar; não cobre todos os modelos nem Wi-Fi. Para recusas específicas, faltam os dados reais ou fictícios da transação e do terminal. A exigência de conta para Pix deve ser respondida por modalidade, sem generalização.

Clima e câmbio atuais devem usar busca web. Datas de depósito de vendas devem usar a tool de recebíveis.

## Próxima etapa do desafio

Estas sínteses são uma base inicial revisável. Para demonstrar o requisito de ingestão de URLs, a aplicação ainda deve implementar um comando reproduzível que consulte as fontes, extraia conteúdo, gere chunks e embeddings e atualize o armazenamento. A existência destes arquivos, sozinha, não demonstra esse pipeline.
