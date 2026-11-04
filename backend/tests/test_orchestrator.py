import json
import pytest
from app.application.use_cases.orchestrator import ExecutionError, Orchestrator
from app.domain.identity import Identity
from app.domain.models import Agent, AgentKind, ModelEvent


def agent(id, delegates=(), tools=()):
    return Agent(id, id, id, 'Regra', AgentKind.ROUTER, 'mock', tools, delegates)


class Model:
    def __init__(self, target='support'): self.target, self.calls = target, 0
    async def stream(self, *, model, instructions, inputs, tools):
        self.calls += 1
        if self.calls == 1:
            call = {'type': 'function_call', 'call_id': 'c1', 'name': 'delegate_to_agent', 'arguments': json.dumps({'agent_id': self.target, 'task': 'consulte'})}
            yield ModelEvent('function_call', data=call)
            yield ModelEvent('completed', data={'output': [call], 'usage': None})
        else:
            yield ModelEvent('text_delta', text='Resposta')
            yield ModelEvent('completed', data={'output': [], 'usage': None})


@pytest.mark.asyncio
async def test_delegates_and_streams_final_answer():
    events = []
    async def emit(kind, data): events.append((kind, data))
    agents = {'router': agent('router', ('support',), ('delegate_to_agent',)), 'support': agent('support')}
    answer = await Orchestrator(Model(), agents, {}).run('Teste', Identity('u', 'w', 'admin'), emit)
    assert answer == 'Resposta'
    assert any(kind == 'route.selected' and data['destination'] == 'support' for kind, data in events)
    assert [data['text'] for kind, data in events if kind == 'response.delta'] == ['Resposta']


@pytest.mark.asyncio
async def test_blocks_unlinked_agent():
    async def emit(*args): pass
    agents = {'router': agent('router', (), ('delegate_to_agent',)), 'support': agent('support')}
    with pytest.raises(ExecutionError, match='não autorizado'):
        await Orchestrator(Model(), agents, {}).run('Teste', Identity('u', 'w', 'admin'), emit)
