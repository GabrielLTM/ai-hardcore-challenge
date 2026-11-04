---
id: "10-resultados-get-smart"
title: "Get Smart: códigos de resultado de integração"
source_url: "https://getstore.getnet.com.br/docs/integracao-pagamento/resultados-funcionalidades/"
publisher: "Getnet"
retrieved_on: "2026-11-03"
language: "pt-BR"
region: "BR"
document_type: "curated_summary"
source_language: "pt-BR"
tags: ["get-smart", "codigos", "erros", "integracao"]
limitations: ["Não mapear estes códigos para erros de e-commerce, outras APIs ou modelos sem identificar a origem."]
content_sha256: "7058335d015a89773e3d79a934083f4aff6750cd254211d31da2f4ee57c7e421"
---

# Get Smart: códigos de resultado de integração

## Códigos de resultado

Na integração Get Smart, result 0 representa sucesso da operação. O código 1 representa recusa, com detalhes sobre o servidor ou componente que negou a transação. O código 2 corresponde a cancelamento pelo usuário ou servidor.

O código 3 representa falha ao concluir, que pode envolver conexão, servidor ou emissor. O código 4 indica erro desconhecido. O código 5 representa Pix pendente; a documentação recomenda consultar o status final.

Esses códigos pertencem ao campo result dessa integração. A mensagem resultDetails complementa o diagnóstico; um número isolado exibido em outro produto não estabelece correspondência com essa tabela.
