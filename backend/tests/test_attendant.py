"""Attendant area: demo login and sessions, the handoff cycle (claim, messages, notes, close), the customer's conversation
in human mode and the return to the router with the attendant's words in its history. Local Mongo; skipped when it is down."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from pymongo import AsyncMongoClient, MongoClient
from app.adapters.inbound.http import agents_routes, execution, operator_routes, routes
from app.adapters.inbound.http.errors import register_error_handlers
from app.adapters.inbound.http.execution import Runtime
from app.adapters.inbound.http.operator_auth import issue_session, session_secret
from app.adapters.inbound.http.run_settings import get_run_settings
from app.adapters.outbound.mongo.conversations import MongoConversations
from app.adapters.outbound.mongo.handoffs import MongoHandoffRepository
from app.adapters.outbound.mongo.operators import MongoOperatorRepository
from app.bootstrap.settings import get_settings
from app.domain.identity import Identity
from app.domain.models import Agent, AgentKind
from test_core_orchestrator import say

URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/?directConnection=true')


def _mongo_up() -> bool:
    try:
        MongoClient(URI, serverSelectionTimeoutMS=1000).admin.command('ping')
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mongo_up(), reason='Mongo local indisponível')
CUSTOMER = Identity('cliente1988', 'demo', 'admin', is_demo=True)
ROUTER = Agent('router', 'Roteador', 'Roteia', 'Regra', AgentKind.ROUTER, 'mock', (), ())
PASSWORD = 'senha-de-teste'


class Model:
    """Model double that records what the router received."""
    def __init__(self): self.inputs = []
    async def stream(self, *, model, instructions, inputs, tools, **_):
        self.inputs.append(inputs)
        for event in say('Certo, posso ajudar.'): yield event
    async def embed(self, *, model, texts): return [[0.1] for _ in texts]
    async def hybrid_search(self, workspace_id, query, embedding, limit=5): return []
    async def close(self): pass


@pytest.fixture
async def api(monkeypatch):
    name = f'test_attendant_{uuid.uuid4().hex[:10]}'
    for key, value in {'MONGO_DATABASE': name, 'OPENAI_API_KEY': 'test-key', 'CHAT_WAIT_SECONDS': '10', 'APP_PROFILE': 'local-demo',
                       'OPERATOR_SESSION_SECRET': 'segredo-de-teste-com-tamanho-suficiente', 'OPERATOR_DEMO_PASSWORD': PASSWORD}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear(); get_run_settings.cache_clear(); session_secret.cache_clear()
    client = AsyncMongoClient(URI, serverSelectionTimeoutMS=2000)
    db = client[name]
    model = Model()

    async def snapshot(db_, workspace_id): return 'g1', {'router': ROUTER}
    monkeypatch.setattr(execution, 'load_published_snapshot', snapshot)
    app = FastAPI()
    register_error_handlers(app)
    for module in (routes, agents_routes, operator_routes):
        app.include_router(module.router)
    app.state.mongo, app.state.tasks = client, {}
    app.state.runtime_factory = lambda settings, db_: Runtime(model, model, None, model.close)
    await MongoConversations(db).ensure_indexes()
    handoffs = MongoHandoffRepository(db)
    await handoffs.ensure_indexes()
    operators = MongoOperatorRepository(db)
    await operators.ensure_indexes()
    await operators.seed('demo', PASSWORD)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://t') as http:
        yield SimpleNamespace(http=http, app=app, db=db, model=model, handoffs=handoffs)
    tasks = list(app.state.tasks.values())
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await client.drop_database(name)
    await client.close()
    get_settings.cache_clear(); get_run_settings.cache_clear(); session_secret.cache_clear()


async def login(api, email):
    response = await api.http.post('/api/v1/operator/login', json={'email': email, 'password': PASSWORD})
    assert response.status_code == 200, response.text
    return {'Authorization': f"Bearer {response.json()['token']}"}


async def conversation_with_ticket(api, workspace='demo'):
    conversation = (await api.http.post('/api/v1/conversations', json={'title': 'Quero um humano'})).json()['id']
    who = Identity('cliente1988', workspace, 'admin')
    ticket = await api.handoffs.open(who, run_id=f'run-{uuid.uuid4().hex[:6]}', conversation_id=conversation,
                                     reason='cliente_pediu_humano', summary='Cliente pediu para falar com uma pessoa.')
    return conversation, ticket['protocol']


async def test_demo_accounts_fill_the_login_and_wrong_credentials_get_the_same_answer(api):
    accounts = (await api.http.get('/api/v1/operator/demo-accounts')).json()['items']
    assert [(a['name'], a['email'], a['password']) for a in accounts] == [
        ('Ana', 'ana@atendimento.demo', PASSWORD), ('Bruno', 'bruno@atendimento.demo', PASSWORD)]
    ok = await api.http.post('/api/v1/operator/login', json={'email': ' ANA@atendimento.demo', 'password': PASSWORD})
    assert ok.status_code == 200 and ok.json()['operator']['name'] == 'Ana' and ok.json()['expires_at'].endswith('Z')
    wrong = await api.http.post('/api/v1/operator/login', json={'email': 'ana@atendimento.demo', 'password': 'errada'})
    unknown = await api.http.post('/api/v1/operator/login', json={'email': 'ninguem@atendimento.demo', 'password': PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()['message'] == unknown.json()['message'] and wrong.json()['code'] == unknown.json()['code']
    row = await api.db.operators.find_one({'email': 'ana@atendimento.demo'})
    assert PASSWORD not in str(row) and len(row['password_hash']) == 128


async def test_production_has_no_password_login_nor_demo_accounts(api, monkeypatch):
    monkeypatch.setenv('APP_PROFILE', 'production')
    monkeypatch.setenv('OIDC_ISSUER', 'https://idp.test')
    monkeypatch.setenv('OIDC_AUDIENCE', 'getnet')
    get_settings.cache_clear()
    assert (await api.http.get('/api/v1/operator/demo-accounts')).status_code == 404
    assert (await api.http.post('/api/v1/operator/login', json={'email': 'ana@atendimento.demo', 'password': PASSWORD})).status_code == 404


async def test_sessions_are_checked_and_never_cross_into_customer_or_admin_routes(api):
    assert (await api.http.get('/api/v1/operator/handoffs')).status_code == 401  # the customer has no attendant session
    ana = await login(api, 'ana@atendimento.demo')
    assert (await api.http.get('/api/v1/operator/handoffs', headers=ana)).status_code == 200
    tampered = {'Authorization': ana['Authorization'][:-3] + 'abc'}
    assert (await api.http.get('/api/v1/operator/handoffs', headers=tampered)).status_code == 401
    old, _ = issue_session({'id': 'ana', 'name': 'Ana', 'workspace_id': 'demo'}, now=datetime.now(timezone.utc) - timedelta(hours=9))
    expired = await api.http.get('/api/v1/operator/handoffs', headers={'Authorization': f'Bearer {old}'})
    assert expired.status_code == 401 and 'expirada' in expired.json()['message']
    assert (await api.http.get('/api/v1/conversations', headers=ana)).status_code == 403
    assert (await api.http.get('/api/v1/agents', headers=ana)).status_code == 403


async def test_tickets_of_another_workspace_do_not_exist_for_the_attendant(api):
    _, protocol = await conversation_with_ticket(api, workspace='outro')
    ana = await login(api, 'ana@atendimento.demo')
    assert (await api.http.get(f'/api/v1/operator/handoffs/{protocol}', headers=ana)).status_code == 404
    assert (await api.http.post(f'/api/v1/operator/handoffs/{protocol}/claim', headers=ana)).status_code == 404
    assert (await api.http.get('/api/v1/operator/handoffs', headers=ana)).json()['items'] == []


async def test_one_active_ticket_per_conversation(api):
    conversation, protocol = await conversation_with_ticket(api)
    again = await api.handoffs.open(CUSTOMER, run_id='run-outro', conversation_id=conversation, reason='assunto_sensivel', summary='x')
    assert again['protocol'] == protocol and await api.db.handoffs.count_documents({}) == 1


async def test_full_cycle_from_ticket_to_the_router_with_the_attendant_in_its_history(api):
    conversation, protocol = await conversation_with_ticket(api)
    ana, bruno = await login(api, 'ana@atendimento.demo'), await login(api, 'bruno@atendimento.demo')
    queue = (await api.http.get('/api/v1/operator/handoffs?tab=abertos', headers=ana)).json()['items']
    assert [t['protocol'] for t in queue] == [protocol] and 'timeline' not in queue[0]

    # Human mode: the customer's message reaches the attendant, no run and no model call.
    chat = await api.http.post('/api/v1/chat', json={'message': 'Alguém aí?', 'user_id': 'cliente1988', 'conversation_id': conversation})
    assert chat.status_code == 200 and chat.json()['status'] == 'with_operator' and chat.json()['answer'] is None
    run = await api.http.post(f'/api/v1/conversations/{conversation}/runs', headers={'Idempotency-Key': 'k1'},
                              json={'message': 'Oi?', 'user_id': 'cliente1988'})
    assert run.status_code == 202 and run.json()['run_id'] is None and run.json()['handoff']['status'] == 'aberto'
    assert api.model.inputs == [] and await api.db.runs.count_documents({}) == 0

    # Claim is atomic: Ana and Bruno at the same time, one wins.
    first, second = await asyncio.gather(api.http.post(f'/api/v1/operator/handoffs/{protocol}/claim', headers=ana),
                                         api.http.post(f'/api/v1/operator/handoffs/{protocol}/claim', headers=bruno))
    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409]
    winner, loser = (ana, bruno) if first.status_code == 200 else (bruno, ana)
    winner_name = 'Ana' if winner is ana else 'Bruno'
    assert (second if first.status_code == 200 else first).json()['details']['operator_name'] == winner_name

    assert (await api.http.post(f'/api/v1/operator/handoffs/{protocol}/messages', headers=loser, json={'content': 'Oi'})).status_code == 403
    sent = await api.http.post(f'/api/v1/operator/handoffs/{protocol}/messages', headers=winner, json={'content': 'Olá, vou verificar sua conta.'})
    assert sent.status_code == 201
    assert (await api.http.post(f'/api/v1/operator/handoffs/{protocol}/notes', headers=loser, json={'content': 'Cliente já ligou ontem.'})).status_code == 201

    seen = (await api.http.get(f'/api/v1/conversations/{conversation}/messages')).json()
    assert seen['handoff'] == {'protocol': protocol, 'status': 'em_atendimento', 'operator_name': winner_name}
    roles = [(m['role'], m.get('author_name')) for m in seen['items']]
    assert ('operator', winner_name) in roles and ('system', None) in roles
    assert not any('ligou ontem' in m['content'] for m in seen['items'])  # internal notes never reach the customer

    detail = (await api.http.get(f'/api/v1/operator/handoffs/{protocol}', headers=loser)).json()
    assert detail['handoff']['summary'].startswith('Cliente pediu') and detail['customer']['terminals'] == []
    assert any(e['kind'] == 'note' for e in detail['handoff']['timeline'])

    # Closing needs a resolution and the holder; then the conversation goes back to the router.
    assert (await api.http.post(f'/api/v1/operator/handoffs/{protocol}/close', headers=winner, json={'resolution': '  '})).status_code == 422
    assert (await api.http.post(f'/api/v1/operator/handoffs/{protocol}/close', headers=winner, json={})).status_code == 422
    assert (await api.http.post(f'/api/v1/operator/handoffs/{protocol}/close', headers=loser, json={'resolution': 'x'})).status_code == 403
    closed = await api.http.post(f'/api/v1/operator/handoffs/{protocol}/close', headers=winner, json={'resolution': 'Conta verificada.'})
    assert closed.status_code == 200 and closed.json()['handoff']['status'] == 'encerrado'
    assert (await api.http.post(f'/api/v1/operator/handoffs/{protocol}/close', headers=winner, json={'resolution': 'x'})).status_code == 409
    assert (await api.http.post(f'/api/v1/operator/handoffs/{protocol}/claim', headers=loser)).status_code == 409

    seen = (await api.http.get(f'/api/v1/conversations/{conversation}/messages')).json()
    assert seen['handoff'] is None and seen['items'][-1]['content'].startswith('Atendimento encerrado')
    assert 'Você voltou' in seen['items'][-1]['content']
    detail = (await api.http.get(f'/api/v1/operator/handoffs/{protocol}', headers=winner)).json()
    assert detail['messages'][-1]['content'] == 'Atendimento encerrado. O cliente voltou a falar com o assistente virtual.'  # written for the attendant
    back = await api.http.post('/api/v1/chat', json={'message': 'E agora?', 'user_id': 'cliente1988', 'conversation_id': conversation})
    assert back.status_code == 200 and back.json()['answer'] == 'Certo, posso ajudar.'
    history = api.model.inputs[-1]
    assert {'role': 'assistant', 'content': f'[Atendente {winner_name}] Olá, vou verificar sua conta.'} in history
    assert {'role': 'user', 'content': 'Alguém aí?'} in history
    assert {'role': 'assistant', 'content': '[Aviso do sistema] Atendimento encerrado. Você voltou a falar com o assistente virtual.'} in history
    assert not any(m['content'].startswith(('Atendente ', 'Atendimento encerrado')) for m in history)  # notices only with their label

    kinds = [e['kind'] for e in (await api.handoffs.get('demo', protocol))['timeline']]
    assert kinds == ['opened', 'customer_message', 'customer_message', 'claimed', 'operator_message', 'note', 'closed']
    assert [t['protocol'] for t in (await api.http.get('/api/v1/operator/handoffs?tab=encerrados', headers=ana)).json()['items']] == [protocol]


async def test_the_customer_can_leave_the_queue_until_an_attendant_claims_it(api):
    conversation, protocol = await conversation_with_ticket(api)
    cancel = f'/api/v1/conversations/{conversation}/handoff/cancel'
    left = await api.http.post(cancel)
    assert left.status_code == 200 and left.json()['handoff'] is None
    seen = (await api.http.get(f'/api/v1/conversations/{conversation}/messages')).json()
    assert seen['handoff'] is None and seen['items'][-1]['content'].startswith('Você saiu da fila')
    ticket = await api.db.handoffs.find_one({'protocol': protocol})
    assert ticket['status'] == 'encerrado' and 'active' not in ticket and ticket['timeline'][-1]['kind'] == 'cancelled'
    assert (await api.http.post(cancel)).status_code == 404  # nothing left to cancel

    # Once an attendant is talking, only the attendant closes it.
    conversation, protocol = await conversation_with_ticket(api)
    ana = await login(api, 'ana@atendimento.demo')
    assert (await api.http.post(f'/api/v1/operator/handoffs/{protocol}/claim', headers=ana)).status_code == 200
    refused = await api.http.post(f'/api/v1/conversations/{conversation}/handoff/cancel')
    assert refused.status_code == 409 and refused.json()['details']['operator_name'] == 'Ana'

