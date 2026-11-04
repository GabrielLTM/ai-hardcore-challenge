"""Tool schemas shown to the model and the handlers the backend runs for them.

Customer identity is never a tool argument: handlers take it from the authenticated context only.
A handler may return a private `_usage` ({model, usage}) entry; the orchestrator turns it into `usage.recorded`.
"""
from collections.abc import Sequence
from app.application.use_cases.guardrails import mask
from app.domain.identity import Identity

HANDOFF_REASONS = ('cliente_pediu_humano', 'sem_evidencia_suficiente', 'problema_nao_resolvido', 'assunto_sensivel')
EXPECTED_CONTACT = ('Um operador humano dará continuidade pelos canais oficiais; esta é uma simulação de demonstração, '
                    'sem integração com atendimento real e sem prazo garantido.')

_QUERY = {'type': 'string', 'description': 'Consulta em linguagem natural, completa e autoexplicativa.'}

TOOL_SPECS: dict[str, dict] = {
    'get_receivables': {
        'description': 'Consulta os recebíveis (vendas, situação e previsão de pagamento) do cliente autenticado desta '
                       'conversa. Não recebe identificador de cliente: a identidade vem do sistema. Retorna dados '
                       'fictícios de demonstração; se a lista vier vazia, informe a ausência sem inventar datas ou valores.',
        'properties': {}},
    'get_terminal_status': {
        'description': 'Consulta modelo, conectividade e erros dos terminais (maquininhas) do cliente autenticado desta '
                       'conversa. Não recebe identificador de cliente: a identidade vem do sistema. Retorna dados '
                       'fictícios de demonstração; se vier vazio, informe a ausência sem supor diagnóstico.',
        'properties': {}},
    'search_knowledge': {
        'description': 'Busca híbrida (semântica e textual) nos documentos oficiais e resumos curados da Getnet: '
                       'produtos, maquininhas, Pix, antecipação, link de pagamento e FAQ. Devolve trechos numerados '
                       '(evidence_id) com URL, data de coleta e limitações; cite-os com [n]. Sem trechos relevantes, '
                       'declare a insuficiência de evidência.',
        'properties': {'query': _QUERY}},
    'web_search': {
        'description': 'Pesquisa atual na web para perguntas gerais ou atuais fora da base Getnet (clima, câmbio, '
                       'notícias). Devolve resumo e fontes numeradas (evidence_id). Pode estar indisponível '
                       "(status 'unavailable'): nesse caso informe a falha e não invente a resposta.",
        'properties': {'query': _QUERY}},
    'request_human_handoff': {
        'description': 'Registra o encaminhamento da conversa para um operador humano e devolve o protocolo. Use só quando o '
                       'cliente pedir um humano, quando não houver evidência ou solução, ou em assunto sensível (reclamação '
                       'formal, fraude, contestação). Não inclua CPF, CNPJ, cartão ou conta no resumo. Chamar de novo na mesma '
                       'execução devolve o mesmo protocolo. Simulação de demonstração: não prometa prazo.',
        'properties': {'reason': {'type': 'string', 'enum': list(HANDOFF_REASONS), 'description': 'Motivo do encaminhamento.'},
                       'summary': {'type': 'string', 'description': 'Resumo curto do caso e do que já foi tentado, sem dados sensíveis.'}}},
    'delegate_to_agent': {
        'description': 'Delega uma tarefa a outro agente permitido e devolve a resposta dele. O agente de destino não '
                       'vê a conversa: descreva a tarefa completa e o contexto necessário.',
        'properties': {'agent_id': {'type': 'string', 'description': 'Identificador do agente de destino.'},
                       'task': {'type': 'string', 'description': 'Tarefa completa, com o contexto necessário.'}}},
}


def tool_definition(name: str, destinations: Sequence[str] = ()) -> dict:
    """Strict function definition for `name`; delegation targets are limited to `destinations`."""
    spec = TOOL_SPECS[name]
    properties = {key: dict(value) for key, value in spec['properties'].items()}
    if name == 'delegate_to_agent':
        properties['agent_id']['enum'] = list(destinations)
    return {'type': 'function', 'name': name, 'description': spec['description'],
            'parameters': {'type': 'object', 'properties': properties, 'required': list(properties),
                           'additionalProperties': False}, 'strict': True}


def make_tools(support, knowledge, model, embedding_model, web_search, handoffs=None, run_id=None, conversation_id=None):
    async def receivables(identity: Identity, args: dict):
        return await support.get_receivables(identity)  # args deliberately ignored

    async def terminals(identity: Identity, args: dict):
        return await support.get_terminal_status(identity)  # args deliberately ignored

    async def retrieve(identity: Identity, args: dict):
        query = str(args.get('query', '')).strip()[:500]
        if not query:
            return {'results': []}
        embed_with_usage = getattr(model, 'embed_with_usage', None)  # optional gateway capability
        usage = None
        if embed_with_usage:
            vectors, tokens = await embed_with_usage(model=embedding_model, texts=[query])
            usage = {'model': embedding_model, 'usage': tokens}
        else:
            vectors = await model.embed(model=embedding_model, texts=[query])
        docs = await knowledge.hybrid_search(identity.workspace_id, query, vectors[0])
        return {**({'_usage': usage} if usage else {}), 'results': [{k: d.get(k) for k in (
            'chunk_id', 'source_id', 'title', 'heading', 'text', 'source_url', 'retrieved_on', 'limitations', 'document_type',
            'index_version', 'score')} for d in docs]}

    async def web(identity: Identity, args: dict):
        query = str(args.get('query', '')).strip()[:500]
        if not query:
            return {'status': 'unavailable', 'answer': '', 'sources': [], 'message': 'Consulta vazia.'}
        return await web_search(query)

    async def handoff(identity: Identity, args: dict):
        reason = args.get('reason')
        if reason not in HANDOFF_REASONS:
            return {'error': {'code': 'invalid_reason', 'message': 'Motivo inválido: use um dos valores permitidos.'}}
        ticket = await handoffs.open(identity, run_id=run_id, conversation_id=conversation_id, reason=reason,
                                     summary=mask(str(args.get('summary', '')).strip(), 500))
        return {'protocol': ticket['protocol'], 'status': ticket['status'], 'expected_contact': EXPECTED_CONTACT}

    return {'get_receivables': receivables, 'get_terminal_status': terminals, 'search_knowledge': retrieve, 'web_search': web,
            **({'request_human_handoff': handoff} if handoffs else {})}
