"""Production identity end to end with the local Mongo: users, workspaces and roles are isolated by the verified token.

Runs use the canned fake model through the real run machinery; skipped when Mongo is down.
"""
import asyncio
import uuid
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from pymongo import AsyncMongoClient, MongoClient
from app.adapters.inbound.http import agents_routes, execution, routes
from app.adapters.inbound.http.errors import register_error_handlers
from app.adapters.inbound.http.execution import Runtime
from app.adapters.inbound.http.run_settings import get_run_settings
from app.adapters.outbound.fake.model import FakeModel, get_fake_settings
from app.bootstrap.settings import get_settings
from app.domain.models import Agent, AgentKind
from test_prod_oidc import AUDIENCE, ISSUER, Issuer, KEY, claims, token

URI = 'mongodb://localhost:27017/?directConnection=true'


def _mongo_up() -> bool:
    try:
        MongoClient(URI, serverSelectionTimeoutMS=1000).admin.command('ping')
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mongo_up(), reason='Mongo local indisponível')
AGENTS = {'router': Agent('router', 'Roteador', 'Roteia', 'Regra', AgentKind.ROUTER, 'mock', ('delegate_to_agent',), ('knowledge',)),
          'knowledge': Agent('knowledge', 'Especialista', 'Responde', 'Regra', AgentKind.KNOWLEDGE, 'mock', ('search_knowledge',), ())}


def bearer(sub='alice', workspace='ws1', roles=('operator',)):
    return {'Authorization': f'Bearer {token(claims(sub=sub, workspace_id=workspace, roles=list(roles)))}'}


@pytest.fixture
async def api(monkeypatch):
    name = f'test_prod_{uuid.uuid4().hex[:10]}'
    env = {'APP_PROFILE': 'production', 'OIDC_ISSUER': ISSUER, 'OIDC_AUDIENCE': AUDIENCE, 'MONGO_DATABASE': name, 'OPENAI_API_KEY': 'test-key',
           'CHAT_WAIT_SECONDS': '10', 'SSE_POLL_SECONDS': '0.02', 'RUN_HEARTBEAT_SECONDS': '0.1',
           'FAKE_MODEL_FIRST_TOKEN_MS': '0', 'FAKE_MODEL_DELTA_MS': '0'}
    for key, value in env.items(): monkeypatch.setenv(key, value)
    for cached in (get_settings, get_run_settings, get_fake_settings): cached.cache_clear()
    client = AsyncMongoClient(URI, serverSelectionTimeoutMS=2000)
    fake = FakeModel()

    async def snapshot(db, workspace_id): return 'g1', AGENTS
    monkeypatch.setattr(execution, 'load_published_snapshot', snapshot)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(routes.router)
    app.include_router(agents_routes.router)
    app.state.mongo, app.state.tasks = client, {}
    app.state.oidc = Issuer(KEY).verifier()
    app.state.runtime_factory = lambda settings, db: Runtime(fake, fake, None, fake.close)
    from app.adapters.outbound.mongo.conversations import MongoConversations
    await MongoConversations(client[name]).ensure_indexes()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://t') as http:
        yield SimpleNamespace(http=http, app=app, db=client[name])
    tasks = list(app.state.tasks.values())
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await client.drop_database(name)
    await client.close()
    for cached in (get_settings, get_run_settings, get_fake_settings): cached.cache_clear()


async def chat(api, **who):
    return await api.http.post('/api/v1/chat', headers=bearer(**who), json={'message': 'Qual o prazo do Pix?', 'user_id': who.get('sub', 'alice')})


async def test_the_token_decides_who_owns_what_and_the_body_cannot_override_it(api):
    created = await api.http.post('/api/v1/conversations', headers=bearer(), json={'title': 'Pix'})
    assert created.status_code == 201
    stored = await api.db.conversations.find_one({'id': created.json()['id']})
    assert (stored['user_id'], stored['workspace_id']) == ('alice', 'ws1')  # from `sub` and the workspace claim

    forged = await api.http.post(f"/api/v1/conversations/{created.json()['id']}/runs", headers={**bearer('bob'), 'Idempotency-Key': 'k-12345678'},
                                 json={'message': 'oi', 'user_id': 'alice'})  # bob tries to speak as alice
    assert forged.status_code == 403
    assert await api.db.runs.count_documents({}) == 0


async def test_users_and_workspaces_only_see_their_own_conversations_and_runs(api):
    own = await chat(api)
    assert own.status_code == 200 and own.json()['status'] == 'completed' and 'simulada' in own.json()['answer']
    conversation, run = own.json()['conversation_id'], own.json()['run_id']

    strangers = [('bob', bearer('bob')), ('alice', bearer('alice', workspace='ws2')), ('carol', bearer('carol', roles=('admin',)))]
    for user_id, headers in strangers:  # same sub in another workspace, and an admin, are strangers to this data too
        assert (await api.http.get('/api/v1/conversations', headers=headers)).json()['items'] == []
        for path in (f'/api/v1/conversations/{conversation}/messages', f'/api/v1/runs/{run}', f'/api/v1/runs/{run}/events',
                     f'/api/v1/runs/{run}/evidence'):
            response = await api.http.get(path, headers=headers)
            assert response.status_code == 404 and 'Prazo' not in response.text, (headers, path)
        assert (await api.http.post(f'/api/v1/runs/{run}/cancel', headers=headers)).status_code == 404
        started = await api.http.post(f'/api/v1/conversations/{conversation}/runs', headers={**headers, 'Idempotency-Key': 'k-87654321'},
                                      json={'message': 'oi', 'user_id': user_id})
        assert started.status_code == 404

    mine = await api.http.get('/api/v1/conversations', headers=bearer())
    assert [c['id'] for c in mine.json()['items']] == [conversation]
    events = await api.http.get(f'/api/v1/runs/{run}/events', headers=bearer())
    assert events.status_code == 200 and 'event: run.completed' in events.text and 'event: response.delta' in events.text


async def test_admin_routes_follow_the_role_in_the_token(api):
    assert (await api.http.get('/api/v1/agents', headers=bearer())).status_code == 403
    assert (await api.http.post('/api/v1/agents/router/publish', headers=bearer(), json={})).status_code == 403
    admin = await api.http.get('/api/v1/agents', headers=bearer('root', roles=('admin',)))
    assert admin.status_code == 200
    assert (await api.http.get('/api/v1/agents', headers={'Authorization': 'Bearer x.y.z'})).status_code == 401
