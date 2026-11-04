"""Human escalation: the request_human_handoff tool (Mongo ticket, idempotent per run, backend identity, masked summary)
and the router -> escalation -> tool orchestration with a scripted model (no provider is ever called)."""
import os
import re
from uuid import uuid4
import pytest
from pymongo import AsyncMongoClient
from app.adapters.outbound.mongo.handoffs import MongoHandoffRepository
from app.adapters.outbound.mongo.seed import SEED_AGENTS
from app.application.use_cases.orchestrator import Orchestrator
from app.application.use_cases.tools import HANDOFF_REASONS, make_tools, tool_definition
from app.domain.graph import KNOWN_TOOLS, Node, check_graph
from app.domain.identity import Identity
from app.domain.models import Agent, AgentKind
from test_core_orchestrator import Recorder, Scripted, call, say

URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/?directConnection=true')
WHO = Identity('cliente1988', 'demo', 'admin', is_demo=True)
PROTOCOL = re.compile(r'^ATD-\d{8}-[A-Z2-9]{4}$')


@pytest.fixture
async def db():
    client = AsyncMongoClient(URI, serverSelectionTimeoutMS=1500)
    try:
        await client.admin.command('ping')
    except Exception:
        await client.close()
        pytest.skip('Mongo local indisponível')
    database = client[f'test_handoff_{uuid4().hex[:10]}']
    await MongoHandoffRepository(database).ensure_indexes()
    yield database
    await client.drop_database(database.name)
    await client.close()


def handoff_tool(db, run_id='run-1', conversation_id='conv-1'):
    return make_tools(None, None, None, 'emb', None, MongoHandoffRepository(db), run_id, conversation_id)['request_human_handoff']


async def test_tool_creates_an_open_ticket_with_the_backend_identity(db):
    result = await handoff_tool(db)(WHO, {'reason': 'cliente_pediu_humano', 'summary': 'Quer falar com uma pessoa.',
                                           'user_id': 'outro', 'workspace_id': 'outro'})
    assert set(result) == {'protocol', 'status', 'expected_contact'} and PROTOCOL.match(result['protocol']) and result['status'] == 'aberto'
    assert 'simulação de demonstração' in result['expected_contact'] and 'operador humano' in result['expected_contact']
    row = await db.handoffs.find_one({'protocol': result['protocol']})
    assert (row['workspace_id'], row['user_id'], row['run_id'], row['conversation_id']) == ('demo', 'cliente1988', 'run-1', 'conv-1')
    assert row['status'] == 'aberto' and row['reason'] == 'cliente_pediu_humano' and row['created_at'].year >= 2026
    assert row['created_at'] == row['updated_at']


async def test_same_run_gets_the_same_ticket_and_another_run_a_new_one(db):
    first = await handoff_tool(db)(WHO, {'reason': 'assunto_sensivel', 'summary': 'a'})
    again = await handoff_tool(db)(WHO, {'reason': 'problema_nao_resolvido', 'summary': 'b'})
    other = await handoff_tool(db, run_id='run-2')(WHO, {'reason': 'assunto_sensivel', 'summary': 'a'})
    assert again['protocol'] == first['protocol'] != other['protocol']
    assert await db.handoffs.count_documents({}) == 2
    assert (await db.handoffs.find_one({'protocol': first['protocol']}))['reason'] == 'assunto_sensivel'  # the first call wins


async def test_summary_masks_long_numbers_and_invalid_reason_creates_nothing(db):
    result = await handoff_tool(db)(WHO, {'reason': 'assunto_sensivel', 'summary': 'Contesta cobrança do cartão 4111 1111 1111 1111 e CPF 123.456.789-09.'})
    summary = (await db.handoffs.find_one({'protocol': result['protocol']}))['summary']
    assert '4111' not in summary and '123.456' not in summary and summary.count('[mascarado]') == 2
    bad = await handoff_tool(db, run_id='run-9')(WHO, {'reason': 'qualquer', 'summary': 'x'})
    assert bad['error']['code'] == 'invalid_reason' and await db.handoffs.count_documents({'run_id': 'run-9'}) == 0


async def test_without_a_repository_the_tool_is_not_offered():
    assert 'request_human_handoff' not in make_tools(None, None, None, 'emb', None)


def test_tool_definition_is_strict_with_the_reason_enum_and_the_graph_knows_it():
    definition = tool_definition('request_human_handoff')
    assert definition['strict'] and definition['parameters']['required'] == ['reason', 'summary']
    assert definition['parameters']['properties']['reason']['enum'] == list(HANDOFF_REASONS)
    assert 'request_human_handoff' in KNOWN_TOOLS


def test_seed_graph_has_the_escalation_agent_and_is_valid():
    by_id = {a['id']: a for a in SEED_AGENTS}
    assert by_id['router']['delegates'] == ['support', 'knowledge', 'escalation'] and by_id['support']['delegates'] == ['knowledge', 'escalation']
    escalation = by_id['escalation']
    assert (escalation['name'], escalation['kind'], escalation['tools'], escalation['delegates']) == ('Atendimento Humano', 'custom', ['request_human_handoff'], [])
    assert '(escalation)' in by_id['router']['instructions'] and '(escalation)' in by_id['support']['instructions']
    assert check_graph({a['id']: Node(tuple(a['delegates']), tuple(a['tools'])) for a in SEED_AGENTS}, root='router') == []


class MemoryHandoffs:
    def __init__(self): self.rows = {}
    async def open(self, who, *, run_id, conversation_id, reason, summary):
        return self.rows.setdefault(run_id, {'protocol': 'ATD-20261104-TEST', 'status': 'aberto', 'reason': reason, 'summary': summary, 'who': who})


async def test_router_delegates_to_escalation_which_registers_the_handoff_once():
    handoffs = MemoryHandoffs()
    agents = {'router': Agent('router', 'Roteador', 'r', 'x', AgentKind.ROUTER, 'router', ('delegate_to_agent',), ('escalation',)),
              'escalation': Agent('escalation', 'Atendimento Humano', 'e', 'x', AgentKind.CUSTOM, 'escalation', ('request_human_handoff',), ())}
    model = Scripted(router=[call('delegate_to_agent', agent_id='escalation', task='Cliente pediu humano.'), say('Protocolo ATD-20261104-TEST. Simulação de demonstração.')],
                     escalation=[call('request_human_handoff', reason='cliente_pediu_humano', summary='Pediu humano.'), say('Protocolo ATD-20261104-TEST registrado.')])
    rec = Recorder()
    tools = make_tools(None, None, None, 'emb', None, handoffs, 'run-1', 'conv-1')
    answer = await Orchestrator(model, agents, tools).run('Quero falar com um atendente humano', WHO, rec)
    assert 'ATD-20261104-TEST' in answer
    started = [d['agent_id'] for d in rec.of('agent.started')]
    assert started == ['router', 'escalation'] and rec.of('route.selected')[0]['destination'] == 'escalation'
    tool_events = [d for d in rec.of('tool.started') if d['tool'] == 'request_human_handoff']
    assert tool_events[0]['arguments'] == {'reason': 'cliente_pediu_humano'}
    assert [d['summary'] for d in rec.of('tool.completed') if d['agent_id'] == 'escalation'] == ['Protocolo ATD-20261104-TEST']
    assert list(handoffs.rows) == ['run-1'] and handoffs.rows['run-1']['who'] == WHO
    assert 'request_human_handoff' in [t['name'] for t in model.seen[2]['tools']] and 'delegate_to_agent' not in [t['name'] for t in model.seen[2]['tools']]
