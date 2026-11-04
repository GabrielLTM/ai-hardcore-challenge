---
id: "05-pix-get-smart"
title: "Pix no POS Digital: habilitação e consulta"
source_url: "https://getstore.getnet.com.br/docs/integracao-pagamento/pix/"
publisher: "Getnet"
retrieved_on: "2026-11-03"
language: "pt-BR"
region: "BR"
document_type: "curated_summary"
source_language: "pt-BR"
tags: ["pix", "get-smart", "integracao", "status"]
limitations: ["Escopo restrito ao POS Digital Get Smart; não generalizar seu prazo para toda modalidade Pix.", "A documentação pressupõe domicílio bancário e chave. Não responde universalmente se todos os produtos Pix exigem conta bancária tradicional."]
content_sha256: "a0aab81ef355a64197725f7ca70c84f799181d5f653892af06ea72c10e919ef4"
---

# Pix no POS Digital: habilitação e consulta

## Habilitação

Na documentação do POS Digital, o estabelecimento contrata Pix pelo Portal Minha Conta ou Aplicativo Getnet, informando a chave cadastrada no domicílio bancário que receberá os pagamentos. A documentação descreve recebimento em até dez segundos para esse fluxo.

## Integração e pendências

A iniciação ocorre pelo deeplink Pagamento v3 e utiliza um callerId exclusivo por requisição. O QR Code pode continuar pagável durante sua validade mesmo depois de deixar a tela ou de um cancelamento na interface.

Sem resposta final, a transação permanece pendente. O deeplink Consulta Status permite verificar o resultado final. O desaparecimento do QR Code, isoladamente, não confirma o cancelamento financeiro.
