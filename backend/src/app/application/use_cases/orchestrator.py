"""Agent orchestration independent of HTTP, databases and model providers."""
import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from app.application.ports.model import ModelGateway
from app.application.use_cases.evidence import EvidenceLedger, event_view
from app.application.use_cases.guardrails import build_instructions, mask, wrap_untrusted
from app.application.use_cases.tools import TOOL_SPECS, tool_definition
from app.domain.identity import Identity
from app.domain.models import Agent
from app.domain.pricing import PRICING_VERSION, estimate_cost_usd


Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
Tool = Callable[[Identity, dict[str, Any]], Awaitable[Any]]

TOOL_FAILED = {'error': {'code': 'tool_unavailable',
                         'message': 'A ferramenta não está disponível no momento. Informe isso ao usuário e não invente dados.'}}


@dataclass(slots=True)
class ExecutionLimits:
    max_depth: int = 3
    max_delegations: int = 8
    max_tool_calls: int = 12
    timeout_seconds: float = 120
    max_output_tokens: int | None = 16000  # per-run budget, checked after each model call


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

    async def run(self, message: str, identity: Identity, emit: Emit, history: list[dict[str, str]] | None = None) -> str:
        """`history`: earlier turns of the conversation, given to the router only; delegated tasks stay self-contained."""
        if 'router' not in self.agents:
            raise ExecutionError('O grafo publicado não tem agente roteador.', 'no_router')
        state = {'delegations': 0, 'tools': 0, 'output_tokens': 0}
        try:
            async with asyncio.timeout(self.limits.timeout_seconds):
                return await self._agent('router', message, identity, emit, state, [], root=True, history=history)
        except TimeoutError:
            raise ExecutionError('Tempo limite da execução atingido.', 'limit_timeout') from None

    async def _agent(self, agent_id: str, task: str, identity: Identity, emit: Emit,
                     state: dict[str, int], path: list[str], root: bool = False, history: list[dict[str, str]] | None = None) -> str:
        if agent_id not in self.agents:
            raise ExecutionError('Delegação inválida: agente inexistente', 'delegation_invalid')
        if agent_id in path:
            raise ExecutionError('Delegação inválida: ciclo entre agentes', 'delegation_cycle')
        if len(path) >= self.limits.max_depth:
            raise ExecutionError('Limite de profundidade de delegação atingido', 'limit_depth')
        agent = self.agents[agent_id]
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
            result = {**result, 'sources': self.evidence.add_web_sources(result.get('sources', []), result.get('retrieved_at'))}
            await emit('retrieval.completed', {'agent_id': agent.id, 'source': 'web',
                                               'results': [{k: s.get(k) for k in ('evidence_id', 'title', 'url')} for s in result['sources']]})
            return result, f"{len(result['sources'])} fontes"
        if name == 'request_human_handoff' and isinstance(result, dict) and 'protocol' in result:
            return result, f"Protocolo {result['protocol']}"
        count = len(result.get('items', [])) if isinstance(result, dict) else 0
        return result, f'{count} registros' if name in ('get_receivables', 'get_terminal_status') else 'Concluída'
