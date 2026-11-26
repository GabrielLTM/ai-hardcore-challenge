"""Agent orchestration independent of HTTP, databases and model providers."""
import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from app.application.ports.model import ModelGateway
from app.application.use_cases.orchestration.evidence import EvidenceLedger, event_view
from app.application.use_cases.orchestration.guardrails import build_instructions, mask, wrap_untrusted
from app.application.use_cases.orchestration.tools import TOOL_SPECS, tool_definition
from app.domain.identity import Identity
from app.domain.models import Agent, AgentKind
from app.domain.review import FAILED, REVISE, SKIPPED, VERDICT_SCHEMA, InvalidVerdict, number_claims, parse_verdict
from app.domain.pricing import PRICING_VERSION, estimate_cost_usd


Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
Tool = Callable[[Identity, dict[str, Any]], Awaitable[Any]]

DATA_TOOLS = ('get_receivables', 'get_terminal_status')
REVIEW_HISTORY_TURNS, REVIEW_HISTORY_CHARS = 6, 800  # recent turns the reviewer sees as context, never as evidence
REVIEW_MARGIN_SECONDS = 1.0  # the reviewer stops this long before the run deadline, so its timeout never kills the run
REVISION_NOTE = ('[Revisão interna, não é mensagem do cliente] O cliente ainda não recebeu nenhuma resposta a esta pergunta. '
                 'Um revisor reprovou o rascunho abaixo, que o cliente nunca viu. Escreva a resposta completa do zero, como a '
                 'primeira resposta ao cliente, corrigindo os problemas; busque evidência de novo se precisar e declare a '
                 'limitação quando não houver. Não se refira ao rascunho ("como dito antes", "a orientação anterior") nem '
                 'mencione a revisão.\nProblemas: {issues}\nRascunho reprovado: {draft}')
TOOL_FAILED = {'error': {'code': 'tool_unavailable',
                         'message': 'A ferramenta não está disponível no momento. Informe isso ao usuário e não invente dados.'}}


@dataclass(slots=True)
class ExecutionLimits:
    max_depth: int = 3
    max_delegations: int = 8
    max_tool_calls: int = 12
    timeout_seconds: float = 120
    max_output_tokens: int | None = 16000  # per-run budget, checked after each model call
    max_revisions: int = 1  # reviewer-requested router rounds per run


class ExecutionError(Exception):
    """Run-level failure; `code` is stable for clients, the message is safe to show to the user."""

    def __init__(self, message: str, code: str = 'execution_error'):
        super().__init__(message)
        self.code = code


class Orchestrator:
    def __init__(self, gateway: ModelGateway, agents: dict[str, Agent], tools: dict[str, Tool], limits: ExecutionLimits | None = None):
        self.gateway, self.agents, self.tools = gateway, dict(agents), dict(tools)  # agents = snapshot fixed for this run
        self.limits = limits or ExecutionLimits()
        self.evidence = EvidenceLedger()
        self.review: dict[str, Any] | None = None  # rounds and revision outcome, saved with the run
        self.reviewer = next((a for a in self.agents.values() if a.kind == AgentKind.REVIEWER), None)
        self.live_sent = False  # some root text already reached the client before the verdict
        self.agents_seen: list[str] = []  # agents that ran in the current round, for curation
        self.empty_searches: list[str] = []  # knowledge queries that found nothing, for curation
        self.facts: list[dict[str, Any]] = []  # customer data and handoff results, shown to the reviewer as grounded facts
        self.answers: list[str] = []  # each judged answer (draft, then revision): admin curation only, never sent to the client
        self.history: list[dict[str, str]] = []  # earlier turns, shown to the reviewer as context (what the client or an attendant said)

    def _held(self, state: dict[str, int]) -> bool:
        """Root text waits for the verdict once the answer rests on evidence or customer data."""
        return bool(self.reviewer) and (bool(self.evidence.retrieved) or state.get('data_tools', 0) > 0)

    async def _deliver(self, text: str, emit: Emit) -> None:
        if self.live_sent:  # rare: a preface went out live before the evidence arrived
            await emit('response.reset', {'reason': 'review', 'round': 2})
        await emit('response.delta', {'text': text, 'agent_id': 'router'})
        self.live_sent = True

    async def run(self, message: str, identity: Identity, emit: Emit, history: list[dict[str, str]] | None = None) -> str:
        """`history`: earlier turns of the conversation, given to the router only; delegated tasks stay self-contained."""
        if 'router' not in self.agents:
            raise ExecutionError('O grafo publicado não tem agente roteador.', 'no_router')
        state = {'delegations': 0, 'tools': 0, 'output_tokens': 0, 'data_tools': 0}
        self.history = list(history or [])
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.limits.timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                answer = await self._agent('router', message, identity, emit, state, [], root=True, history=history)
                if self.reviewer:
                    answer = await self._reviewed(message, answer, identity, emit, state, history, deadline)
                return answer
        except TimeoutError:
            raise ExecutionError('Tempo limite da execução atingido.', 'limit_timeout') from None

    async def _reviewed(self, message: str, answer: str, identity: Identity, emit: Emit, state: dict[str, int],
                        history: list[dict[str, str]] | None, deadline: float) -> str:
        """Reviewer pass and at most `max_revisions` router rounds; the reviewer never fails the run (fail-open)."""
        if not self.evidence.retrieved and not state['data_tools']:
            self.review = {'rounds': [], 'revision': 'not_needed', 'empty_searches': self.empty_searches}
            await emit('review.completed', {'agent_id': self.reviewer.id, 'round': 1, 'verdict': SKIPPED, 'revision': 'not_needed'})
            return answer
        first = await self.judge(1, message, answer, emit, state, deadline)
        self.answers = [answer]
        revision = 'not_needed'
        if first['verdict'] == REVISE and self.limits.max_revisions > 0:
            budget, used = self.limits.max_output_tokens, state['output_tokens']
            left = deadline - asyncio.get_running_loop().time()
            fits = (not budget or used + len(answer) // 4 <= budget) and left > 0.2 * self.limits.timeout_seconds
            revision = 'done' if fits else 'skipped_budget'
        self.review = {'rounds': [first], 'revision': revision, 'empty_searches': self.empty_searches}
        await emit('review.completed', {**first, 'revision': revision})
        if revision != 'done':
            await self._deliver(answer, emit)
            return answer
        self.agents_seen = []
        prior = [*(history or []), {'role': 'user', 'content': message}]  # the draft never reached the client: not a turn
        try:
            async with asyncio.timeout_at(deadline - REVIEW_MARGIN_SECONDS):
                revised = await self._agent('router', REVISION_NOTE.format(issues='; '.join(first['issues']), draft=answer), identity, emit, state, [],
                                            root=True, history=prior)
        except (ExecutionError, TimeoutError) as exc:  # keep the first answer rather than leaving the client with nothing
            self.review['revision'] = 'failed'
            await emit('review.completed', {'agent_id': self.reviewer.id, 'round': 2, 'verdict': FAILED,
                                            'code': getattr(exc, 'code', 'limit_timeout'), 'revision': 'failed'})
            await self._deliver(answer, emit)
            return answer
        second = await self.judge(2, message, revised, emit, state, deadline)
        self.answers.append(revised)
        self.review['rounds'].append(second)
        await emit('review.completed', {**second, 'revision': 'done'})
        await self._deliver(revised, emit)
        return revised

    async def judge(self, round_: int, message: str, answer: str, emit: Emit, state: dict[str, int], deadline: float) -> dict[str, Any]:
        agent = self.reviewer
        await emit('review.started', {'agent_id': agent.id, 'round': round_})
        _, invalid = self.evidence.resolve(answer)
        evidence = [{k: i.get(k) for k in ('evidence_id', 'kind', 'title', 'heading', 'url', 'text', 'limitations')}
                    for i in self.evidence.retrieved]
        review_input = {'pergunta': message, 'resposta': answer, 'citacoes_invalidas': invalid,
                        'numeros_sem_citacao': number_claims(answer), 'ferramentas_de_dados_chamadas': state['data_tools'],
                        'atendimento_humano_disponivel': any('request_human_handoff' in a.tools for a in self.agents.values())}
        inputs = [{'role': 'user', 'content': wrap_untrusted('revisao', review_input) + '\n\n' + wrap_untrusted('evidencias', evidence)
                   + '\n\n' + wrap_untrusted('dados_de_ferramentas', self.facts)
                   + ('\n\n' + wrap_untrusted('historico_da_conversa', [
                       {'role': t.get('role'), 'content': str(t.get('content', ''))[:REVIEW_HISTORY_CHARS]}
                       for t in self.history[-REVIEW_HISTORY_TURNS:]]) if self.history else '')}]
        cap = {'max_output_tokens': agent.limits['max_output_tokens']} if agent.limits.get('max_output_tokens') else {}
        result: dict[str, Any] = {'agent_id': agent.id, 'round': round_, 'agents': list(dict.fromkeys(self.agents_seen)),
                                  'sources': [{k: i.get(k) for k in ('evidence_id', 'kind', 'source_id', 'title', 'url')}
                                              for i in self.evidence.retrieved]}
        started = asyncio.get_running_loop().time()
        text: list[str] = []
        try:
            async with asyncio.timeout_at(deadline - REVIEW_MARGIN_SECONDS):
                async for event in self.gateway.stream(model=agent.model, instructions=build_instructions(agent.instructions, [], ()),
                                                       inputs=inputs, tools=[], response_schema=VERDICT_SCHEMA, **cap):
                    if event.type == 'text_delta':
                        text.append(event.text)
                    elif event.type == 'completed':
                        usage = event.data.get('usage')
                        cost = estimate_cost_usd(agent.model, usage)
                        result['cost_usd'] = cost
                        await emit('usage.recorded', {'agent_id': agent.id, 'model': agent.model, 'usage': usage,
                                                      'cost_usd': cost, 'pricing_version': PRICING_VERSION})
                        state['output_tokens'] += (usage or {}).get('output_tokens') or 0
                    elif event.type == 'error':
                        raise ExecutionError('Falha do modelo no Revisor', 'model_error')
            verdict = parse_verdict(''.join(text))
            result.update(verdict=verdict.verdict, scores=verdict.scores, issues=[mask(i, 500) for i in verdict.issues])
        except (ExecutionError, InvalidVerdict, TimeoutError) as exc:
            code = getattr(exc, 'code', None) or ('invalid_verdict' if isinstance(exc, InvalidVerdict) else 'review_timeout')
            result.update(verdict=FAILED, code=code, issues=[])
        result['duration_ms'] = round((asyncio.get_running_loop().time() - started) * 1000)
        return result

    async def _agent(self, agent_id: str, task: str, identity: Identity, emit: Emit,
                     state: dict[str, int], path: list[str], root: bool = False, history: list[dict[str, str]] | None = None) -> str:
        if agent_id not in self.agents:
            raise ExecutionError('Delegação inválida: agente inexistente', 'delegation_invalid')
        if agent_id in path:
            raise ExecutionError('Delegação inválida: ciclo entre agentes', 'delegation_cycle')
        if len(path) >= self.limits.max_depth:
            raise ExecutionError('Limite de profundidade de delegação atingido', 'limit_depth')
        agent = self.agents[agent_id]
        self.agents_seen.append(agent.id)
        await emit('agent.started', {'agent_id': agent.id, 'name': agent.name, 'version': agent.version,
                                     'parent_agent_id': path[-1] if path else None})
        try:
            return await self._converse(agent, task, identity, emit, state, path, root, history)
        except ExecutionError as exc:
            await emit('agent.failed', {'agent_id': agent.id, 'version': agent.version, 'code': exc.code, 'message': str(exc)})
            raise

    async def _converse(self, agent: Agent, task: str, identity: Identity, emit: Emit,
                        state: dict[str, int], path: list[str], root: bool, history: list[dict[str, str]] | None = None) -> str:
        destinations = [k for k in agent.delegates if k in self.agents]
        catalog = [{'id': k, 'name': self.agents[k].name, 'description': self.agents[k].description} for k in destinations]
        instructions = build_instructions(agent.instructions, catalog, agent.tools)
        inputs: list[dict[str, Any]] = [*(history or []), {'role': 'user', 'content': task}]
        offered = [n for n in agent.tools if n in TOOL_SPECS and (bool(destinations) if n == 'delegate_to_agent' else n in self.tools)]
        definitions = [tool_definition(name, destinations) for name in offered]
        streamed: list[str] = []  # root text exactly as streamed, across turns
        while True:
            calls: list[dict[str, Any]] = []
            output: list[dict[str, Any]] = []
            text_parts: list[str] = []
            cap = {'max_output_tokens': agent.limits['max_output_tokens']} if agent.limits.get('max_output_tokens') else {}
            async for event in self.gateway.stream(model=agent.model, instructions=instructions, inputs=inputs, tools=definitions, **cap):
                if event.type == 'text_delta':
                    text_parts.append(event.text)
                    if root:
                        streamed.append(event.text)
                        if not self._held(state):
                            self.live_sent = True
                            await emit('response.delta', {'text': event.text, 'agent_id': agent.id})
                elif event.type == 'function_call':
                    calls.append(event.data)
                elif event.type == 'completed':
                    output = event.data.get('output', [])
                    usage = event.data.get('usage')
                    await emit('usage.recorded', {'agent_id': agent.id, 'model': agent.model, 'usage': usage,
                                                  'cost_usd': estimate_cost_usd(agent.model, usage), 'pricing_version': PRICING_VERSION})
                    state['output_tokens'] += (usage or {}).get('output_tokens') or 0
                elif event.type == 'error':
                    raise ExecutionError('Falha ao gerar resposta do modelo', 'model_error')
            budget = self.limits.max_output_tokens
            if budget and state['output_tokens'] > budget:
                raise ExecutionError('Limite de tokens de saída da execução atingido', 'limit_output_tokens')
            if not calls:
                await emit('agent.completed', {'agent_id': agent.id, 'version': agent.version})
                return ''.join(streamed if root else text_parts)
            inputs.extend(output or calls)
            for call in calls:
                result = await self._call(agent, call, identity, emit, state, path)
                inputs.append({'type': 'function_call_output', 'call_id': call['call_id'],
                               'output': wrap_untrusted(call.get('name', ''), result)})

    async def _refuse(self, emit: Emit, agent: Agent, tool: str, message: str, code: str):
        await emit('tool.failed', {'agent_id': agent.id, 'tool': tool, 'code': code, 'message': message})
        raise ExecutionError(message, code)

    async def _call(self, agent: Agent, call: dict[str, Any], identity: Identity, emit: Emit,
                    state: dict[str, int], path: list[str]) -> Any:
        name = call.get('name', '')
        state['tools'] += 1
        if state['tools'] > self.limits.max_tool_calls:
            await self._refuse(emit, agent, name, 'Limite de chamadas de ferramentas atingido', 'limit_tool_calls')
        state[f'tools:{agent.id}'] = used = state.get(f'tools:{agent.id}', 0) + 1
        if agent.limits.get('max_tool_calls') and used > agent.limits['max_tool_calls']:
            await self._refuse(emit, agent, name, 'Limite de chamadas de ferramentas do agente atingido', 'limit_tool_calls')
        if name not in agent.tools or name not in TOOL_SPECS:
            await self._refuse(emit, agent, name, 'Ferramenta não autorizada', 'tool_not_allowed')
        try:
            args = json.loads(call.get('arguments') or '{}')
            if not isinstance(args, dict):
                raise ValueError
        except (ValueError, TypeError):
            await self._refuse(emit, agent, name, 'Argumentos inválidos', 'invalid_arguments')
        shown = {'agent_id': mask(str(args.get('agent_id', '')), 80), 'task': mask(str(args.get('task', '')))} \
            if name == 'delegate_to_agent' else {'query': mask(str(args['query']))} if 'query' in args else \
            {'reason': mask(str(args.get('reason', '')), 40)} if name == 'request_human_handoff' else {}
        await emit('tool.started', {'agent_id': agent.id, 'tool': name, 'arguments': shown})
        if name == 'delegate_to_agent':
            target = args.get('agent_id')
            if target not in agent.delegates or target not in self.agents:
                await self._refuse(emit, agent, name, 'Agente de destino não autorizado', 'delegation_not_allowed')
            state['delegations'] += 1
            if state['delegations'] > self.limits.max_delegations:
                await self._refuse(emit, agent, name, 'Limite de delegações atingido', 'limit_delegations')
            await emit('route.selected', {'agent_id': agent.id, 'destination': target})
            result = await self._agent(target, str(args.get('task', '')), identity, emit, state, [*path, agent.id])
            summary = f'Resposta de {target}'
        elif name in self.tools:
            try:
                result = await self.tools[name](identity, args)
            except Exception:  # provider/database outage: the model must tell the user, not guess
                await emit('tool.failed', {'agent_id': agent.id, 'tool': name, 'code': 'tool_unavailable',
                                           'message': 'Ferramenta indisponível no momento.'})
                return TOOL_FAILED
            if isinstance(result, dict) and '_usage' in result:  # e.g. embedding tokens spent by the tool; never shown to the model
                result = dict(result)
                spent = result.pop('_usage')
                await emit('usage.recorded', {'agent_id': agent.id, 'model': spent['model'], 'usage': spent['usage'],
                                              'cost_usd': estimate_cost_usd(spent['model'], spent['usage']), 'pricing_version': PRICING_VERSION})
            if name in DATA_TOOLS:
                state['data_tools'] = state.get('data_tools', 0) + 1
            if name in (*DATA_TOOLS, 'request_human_handoff') and isinstance(result, dict) and 'error' not in result:
                self.facts.append({'ferramenta': name, 'resultado': result})
            if name == 'search_knowledge' and isinstance(result, dict) and not result.get('results'):
                self.empty_searches.append(mask(str(args.get('query', ''))))
            result, summary = await self._register(agent, name, result, emit)
            if isinstance(result, dict) and result.get('status') == 'unavailable':
                await emit('tool.failed', {'agent_id': agent.id, 'tool': name, 'code': 'unavailable',
                                           'message': 'Fonte indisponível no momento.'})
                return result
        else:
            await self._refuse(emit, agent, name, 'Ferramenta indisponível', 'tool_unavailable')
        await emit('tool.completed', {'agent_id': agent.id, 'tool': name, 'summary': summary})
        return result

    async def _register(self, agent: Agent, name: str, result: Any, emit: Emit) -> tuple[Any, str]:
        """Number retrieved passages/sources for citation and announce them; returns (model-facing result, summary)."""
        if name == 'search_knowledge':
            items = self.evidence.add_passages(result.get('results', []))
            by_id = {i['evidence_id']: i for i in self.evidence.retrieved}
            await emit('retrieval.completed', {'agent_id': agent.id, 'source': 'knowledge',
                                               'results': [event_view(by_id[i['evidence_id']]) for i in items]})
            return {**result, 'results': items}, f'{len(items)} trechos recuperados'
        if name == 'web_search' and result.get('status') != 'unavailable':
            result = {**result, 'sources': self.evidence.add_web_sources(result.get('sources', []), result.get('retrieved_at'),
                                                                         result.get('answer') or '')}
            await emit('retrieval.completed', {'agent_id': agent.id, 'source': 'web',
                                               'results': [{k: s.get(k) for k in ('evidence_id', 'title', 'url')} for s in result['sources']]})
            return result, f"{len(result['sources'])} fontes"
        if name == 'request_human_handoff' and isinstance(result, dict) and 'protocol' in result:
            return result, f"Protocolo {result['protocol']}"
        count = len(result.get('items', [])) if isinstance(result, dict) else 0
        return result, f'{count} registros' if name in ('get_receivables', 'get_terminal_status') else 'Concluída'
