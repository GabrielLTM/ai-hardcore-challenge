"""HTTP/Mongo integration of runs: chat, idempotency, SSE, cancellation, lease, evidence and isolation.

Uses the local Mongo (replica set) with a throw-away database and a scripted model; skipped when Mongo is down.
"""
import asyncio
import codecs
import json
import uuid
from datetime import datetime
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from pymongo import AsyncMongoClient, MongoClient
from app.adapters.inbound.http import execution, routes
from app.adapters.inbound.http.errors import register_error_handlers
from app.adapters.inbound.http.execution import Runtime
from app.adapters.inbound.http.run_settings import get_run_settings
from app.adapters.outbound.mongo.conversations import MongoConversations
from app.adapters.outbound.mongo.support import MongoSupportRepository
from app.bootstrap.settings import get_settings
from app.domain.errors import ConversationNotFound
from app.domain.identity import Identity
from app.domain.models import Agent, AgentKind, ModelEvent
from test_core_orchestrator import CHUNK, call, say

URI = 'mongodb://localhost:27017/?directConnection=true'


def _mongo_up() -> bool:
    try:
        MongoClient(URI, serverSelectionTimeoutMS=1000).admin.command('ping')
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mongo_up(), reason='Mongo local indisponível')
ROUTER = Agent('router', 'Roteador', 'Roteia', 'Regra', AgentKind.ROUTER, 'mock', ('search_knowledge', 'get_receivables'), ())
DEMO = Identity('cliente1988', 'demo', 'admin', is_demo=True)


async def blocking():
    """A model turn that streams some text and then never finishes (until cancelled)."""
    yield ModelEvent('text_delta', text='Olá ')
    yield ModelEvent('text_delta', text='mundo')
    execution_state.blocked.set()
    await asyncio.Event().wait()


execution_state = SimpleNamespace(blocked=asyncio.Event())


class Control:
    def __init__(self): self.turns, self.calls = [], 0
    async def stream(self, *, model, instructions, inputs, tools):
        self.calls += 1
        turn = self.turns.pop(0)
        if callable(turn):
            async for event in turn(): yield event
        else:
            for event in turn: yield event
    async def embed(self, *, model, texts): return [[0.1] for _ in texts]
    async def hybrid_search(self, workspace_id, query, embedding, limit=5):
        assert workspace_id == 'demo'
        return [CHUNK]
    async def close(self): pass


@pytest.fixture
async def api(monkeypatch):
    name = f'test_core_{uuid.uuid4().hex[:10]}'
    for key, value in {'MONGO_DATABASE': name, 'OPENAI_API_KEY': 'test-key', 'CHAT_WAIT_SECONDS': '10', 'CANCEL_WAIT_SECONDS': '3',
                       'RUN_HEARTBEAT_SECONDS': '0.1', 'SSE_POLL_SECONDS': '0.02'}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear(); get_run_settings.cache_clear()
    execution_state.blocked = asyncio.Event()
    client = AsyncMongoClient(URI, serverSelectionTimeoutMS=2000)
    db = client[name]
    control = Control()

    async def snapshot(db_, workspace_id): return 'g1', {'router': ROUTER}
    monkeypatch.setattr(execution, 'load_published_snapshot', snapshot)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(routes.router)
    app.state.mongo, app.state.tasks = client, {}
    app.state.runtime_factory = lambda settings, db_: Runtime(control, control, None, control.close)
    repo = MongoConversations(db)
    await repo.ensure_indexes()

    def settings(**env):  # change run settings inside a test
        for key, value in env.items(): monkeypatch.setenv(key, value)
        get_run_settings.cache_clear()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://t') as http:
        yield SimpleNamespace(http=http, app=app, db=db, repo=repo, ctl=control, settings=settings)
    tasks = list(app.state.tasks.values())
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await client.drop_database(name)
    await client.close()
    get_settings.cache_clear(); get_run_settings.cache_clear()


def parse_sse(text: str) -> list[dict]:
    frames = []
    for block in text.split('\n\n'):
        if not block.strip(): continue
        if block.startswith(':'):
            frames.append({'comment': block}); continue
        frame = dict(line.split(': ', 1) for line in block.split('\n'))
        frame['data'] = json.loads(frame['data'])
        frames.append(frame)
    return frames


async def chat(api, message='Qual o prazo do Pix?', **extra):
    return await api.http.post('/api/v1/chat', json={'message': message, 'user_id': 'cliente1988', **extra})


async def wait_status(api, run_id, *statuses, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        run = (await api.http.get(f'/api/v1/runs/{run_id}')).json()
        if run['status'] in statuses: return run
        await asyncio.sleep(0.02)
    raise AssertionError(f'run stayed {run["status"]}')


async def start_blocked_chat(api, **settings):
    api.settings(CHAT_WAIT_SECONDS='0.05', **settings)
    api.ctl.turns = [blocking]
    response = await chat(api)
    assert response.status_code == 202
    await asyncio.wait_for(execution_state.blocked.wait(), 5)
    return response.json()


async def new_run(api, who=DEMO, key='k', message='m', lease=60):
    conversation = await api.repo.create_conversation(who)
    run, _ = await api.repo.create_run(who, conversation['id'], message, key, lease_seconds=lease)
    await api.repo.claim_run(who, run['id'], 'test', lease)
    return conversation, run


async def completed_run(api):
    chunks = ['á' * 250 + ' ', 'ç' * 250 + ' ', 'não 🙂 ' + 'x' * 250, 'fim [1]']
    api.ctl.turns = [call('search_knowledge', query='pix'),
                     [*(ModelEvent('text_delta', text=c) for c in chunks), *say('', {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})[1:]]]
    return (await chat(api)).json(), ''.join(chunks)


# ---------- POST /chat ----------

async def test_chat_returns_answer_sources_trace_and_ids_and_records_evidence_usage(api):
    api.ctl.turns = [call('search_knowledge', query='prazo pix'),
                     say('Depende da modalidade [1]; outra [7].', {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})]
    response = await chat(api)
    body = response.json()
    assert response.status_code == 200 and body['status'] == 'completed' and response.headers['x-request-id']
    assert set(body) == {'conversation_id', 'run_id', 'answer', 'sources', 'trace_id', 'status'}
    assert body['answer'] == 'Depende da modalidade [1]; outra [7].' and len(body['trace_id']) == 32
    assert [(s['evidence_id'], s['url'], s['title']) for s in body['sources']] == [(1, 'https://getnet.test/pix', 'Pix')]

    run = (await api.http.get(f"/api/v1/runs/{body['run_id']}")).json()
    assert run['graph_version'] == 'g1' and run['final'] and run['status'] == 'completed' and run['conversation_id'] == body['conversation_id']
    assert run['usage']['total_tokens'] == 15 and run['usage']['cost_usd'] is None  # unknown price stays unknown
    assert run['created_at'].endswith('Z') and 'request_hash' not in run and 'workspace_id' not in run
    evidence = (await api.http.get(f"/api/v1/runs/{body['run_id']}/evidence")).json()
    assert evidence['invalid_citations'] == [7] and len(evidence['cited']) == 1
    item = evidence['retrieved'][0]
    assert item['cited'] is True and item['kind'] == 'curated' and item['version'] == 'v1' and item['retrieved_on'] == '2026-11-03'
    assert item['limitations'] == [] and item['title'] == 'Pix' and item['url'] == 'https://getnet.test/pix' and item['text']
    messages = (await api.http.get(f"/api/v1/conversations/{body['conversation_id']}/messages")).json()['items']
    assert [m['role'] for m in messages] == ['user', 'assistant'] and messages[1]['sources'][0]['evidence_id'] == 1
    assert 'active_run_id' not in await api.db.conversations.find_one({'id': body['conversation_id']})


async def test_chat_timeout_answers_202_with_ids_and_a_consultable_state(api):
    started = await start_blocked_chat(api)
    assert started['status'] in ('queued', 'running') and started['answer'] is None and started['sources'] == []
    assert started['run_id'] and started['conversation_id'] and started['events_url'].endswith(f"{started['run_id']}/events")
    assert (await api.http.get(f"/api/v1/runs/{started['run_id']}")).json()['status'] == 'running'


async def test_chat_validation_and_identity_errors_are_structured(api):
    wrong = await api.http.post('/api/v1/chat', json={'message': 'oi', 'user_id': 'outra-pessoa'})
    assert wrong.status_code == 403 and wrong.json()['code'] == 'forbidden' and wrong.json()['request_id']
    invalid = await api.http.post('/api/v1/chat', json={'user_id': 'cliente1988'})
    assert invalid.status_code == 422 and invalid.json()['code'] == 'validation_error' and invalid.json()['details']
    assert (await api.http.get('/api/v1/runs/nao-existe')).json()['code'] == 'run_not_found'


async def test_chat_failure_is_a_structured_502_and_the_run_keeps_the_sanitized_error(api):
    api.ctl.turns = [[ModelEvent('error', data={'secret': 'sk-123'})]]
    response = await chat(api)
    body = response.json()
    assert response.status_code == 502 and body['code'] == 'run_failed' and body['details']['error_code'] == 'model_error'
    assert 'sk-123' not in response.text
    run = (await api.http.get(f"/api/v1/runs/{body['details']['run_id']}")).json()
    assert run['status'] == 'failed' and run['error']['code'] == 'model_error' and not run['final']
    events = parse_sse((await api.http.get(f"/api/v1/runs/{run['id']}/events")).text)
    assert events[-1]['event'] == 'run.failed' and events[-1]['data']['payload']['code'] == 'model_error'


async def test_capacity_is_limited_per_workspace_and_existing_runs_survive(api):
    first = await start_blocked_chat(api, MAX_ACTIVE_RUNS='1')
    second = await chat(api)
    assert second.status_code == 429 and second.json()['code'] == 'capacity_exceeded' and second.headers['retry-after']
    assert await api.db.conversations.count_documents({}) == 1  # the refused request left no empty conversation
    assert (await api.http.get(f"/api/v1/runs/{first['run_id']}")).json()['status'] == 'running'


# ---------- idempotency ----------

async def test_same_key_and_body_replays_the_same_run_and_other_body_conflicts(api):
    conversation = (await api.http.post('/api/v1/conversations', json={'title': 't'})).json()
    url = f"/api/v1/conversations/{conversation['id']}/runs"
    api.ctl.turns = [blocking]
    body = {'message': 'primeira', 'user_id': 'cliente1988'}
    first = await api.http.post(url, json=body, headers={'Idempotency-Key': 'k1'})
    again = await api.http.post(url, json=body, headers={'Idempotency-Key': 'k1'})
    assert first.status_code == again.status_code == 202 and first.json()['run_id'] == again.json()['run_id']
    conflict = await api.http.post(url, json={**body, 'message': 'outra'}, headers={'Idempotency-Key': 'k1'})
    assert conflict.status_code == 409 and conflict.json()['code'] == 'idempotency_conflict'
    busy = await api.http.post(url, json=body, headers={'Idempotency-Key': 'k2'})
    assert busy.status_code == 409 and busy.json()['code'] == 'conversation_busy'
    missing = await api.http.post('/api/v1/conversations/nao-existe/runs', json=body, headers={'Idempotency-Key': 'k3'})
    assert missing.status_code == 404 and missing.json()['code'] == 'conversation_not_found'
    assert await api.db.runs.count_documents({}) == 1 and await api.db.messages.count_documents({'role': 'user'}) == 1
    await asyncio.wait_for(execution_state.blocked.wait(), 5)
    assert api.ctl.calls == 1
    await api.http.post(f"/api/v1/runs/{first.json()['run_id']}/cancel")
    replay = await api.http.post(url, json=body, headers={'Idempotency-Key': 'k1'})  # after a terminal state: still the original run
    assert replay.json()['run_id'] == first.json()['run_id'] and replay.json()['status'] == 'cancelled'
    assert api.ctl.calls == 1 and await api.db.messages.count_documents({'role': 'user'}) == 1


async def test_concurrent_requests_with_the_same_key_create_one_run(api):
    conversation = await api.repo.create_conversation(DEMO)
    results = await asyncio.gather(*[api.repo.create_run(DEMO, conversation['id'], 'm', 'same') for _ in range(5)],
                                   return_exceptions=True)
    assert all(isinstance(r, tuple) for r in results)  # racers replay the winner instead of failing as busy
    assert len({run['id'] for run, _ in results}) == 1 and sum(created for _, created in results) == 1
    assert await api.db.runs.count_documents({}) == 1


# ---------- SSE ----------

async def test_sse_contract_replay_without_duplicates_and_fragmented_utf8(api):
    body, text = await completed_run(api)
    rid = body['run_id']
    response = await api.http.get(f'/api/v1/runs/{rid}/events')
    assert response.headers['content-type'] == 'text/event-stream; charset=utf-8' and response.headers['cache-control'] == 'no-cache'
    frames = parse_sse(response.content.decode('utf-8'))
    n = len(frames)
    assert [int(f['id']) for f in frames] == list(range(1, n + 1))
    assert frames[0]['event'] == 'run.started' and frames[-1]['event'] == 'run.completed'
    for f in frames:
        data = f['data']
        assert f['event'] == data['type'] and data['seq'] == int(f['id']) and data['run_id'] == rid
        assert data['occurred_at'].endswith('Z') and 'workspace_id' not in data and 'user_id' not in data
    deltas = [f['data']['payload']['text'] for f in frames if f['event'] == 'response.delta']
    assert ''.join(deltas) == text == body['answer']  # each character exactly once, accents and emoji intact
    kinds = {f['event'] for f in frames}
    assert {'run.started', 'agent.started', 'tool.started', 'retrieval.completed', 'tool.completed', 'usage.recorded', 'agent.completed'} <= kinds
    assert frames[-1]['data']['payload']['cited_evidence'] == [1]

    cut = n // 2
    resumed = parse_sse((await api.http.get(f'/api/v1/runs/{rid}/events', headers={'Last-Event-ID': str(cut)})).text)
    assert [int(f['id']) for f in resumed] == list(range(cut + 1, n + 1))
    header_wins = await api.http.get(f'/api/v1/runs/{rid}/events?after=0', headers={'Last-Event-ID': str(cut)})
    assert len(parse_sse(header_wins.text)) == n - cut
    assert len(parse_sse((await api.http.get(f'/api/v1/runs/{rid}/events?after={n}')).text)) == 0

    decoder, buffer, fragmented = codecs.getincrementaldecoder('utf-8')(), '', []
    for i in range(0, len(response.content), 7):  # frames cut at arbitrary bytes, inside multi-byte characters
        buffer += decoder.decode(response.content[i:i + 7])
        while '\n\n' in buffer:
            block, buffer = buffer.split('\n\n', 1)
            fragmented.append(block)
    assert fragmented == [block for block in response.text.split('\n\n') if block]


async def test_reconnecting_on_another_instance_resumes_from_the_shared_database(api):
    body, text = await completed_run(api)
    other = FastAPI()  # a second API instance: same database, no shared memory with the one that ran the run
    register_error_handlers(other)
    other.include_router(routes.router)
    other.state.mongo, other.state.tasks = api.app.state.mongo, {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=other), base_url='http://t') as http:
        total = len(parse_sse((await api.http.get(f"/api/v1/runs/{body['run_id']}/events")).text))
        resumed = parse_sse((await http.get(f"/api/v1/runs/{body['run_id']}/events", headers={'Last-Event-ID': '2'})).text)
        assert [int(f['id']) for f in resumed] == list(range(3, total + 1)) and resumed[-1]['event'] == 'run.completed'
        assert (await http.get(f"/api/v1/runs/{body['run_id']}")).json()['partial_text'] == text


@pytest.mark.parametrize('query,headers', [('after=abc', {}), ('after=-1', {}), ('after=1.5', {}), ('after=9999', {}), ('', {'Last-Event-ID': 'x'})])
async def test_invalid_cursor_is_400_before_the_stream_opens(api, query, headers):
    body, _ = await completed_run(api)
    response = await api.http.get(f"/api/v1/runs/{body['run_id']}/events?{query}", headers=headers)
    assert response.status_code == 400 and response.json()['code'] == 'invalid_cursor'
    assert response.headers['content-type'].startswith('application/json')


async def test_cursor_older_than_retention_is_410_before_the_stream_opens(api):
    body, _ = await completed_run(api)
    rid = body['run_id']
    await api.db.run_events.delete_many({'run_id': rid, 'seq': {'$lte': 3}})  # what the TTL index does after the retention period
    gone = await api.http.get(f'/api/v1/runs/{rid}/events?after=0')
    assert gone.status_code == 410 and gone.json()['code'] == 'cursor_expired' and gone.json()['details']['first_available_seq'] == 4
    assert (await api.http.get(f'/api/v1/runs/{rid}/events?after=2')).status_code == 410
    ok = await api.http.get(f'/api/v1/runs/{rid}/events', headers={'Last-Event-ID': '3'})
    assert ok.status_code == 200 and int(parse_sse(ok.text)[0]['id']) == 4
    await api.db.run_events.delete_many({'run_id': rid})
    assert (await api.http.get(f'/api/v1/runs/{rid}/events?after=0')).status_code == 410
    assert (await api.http.get(f'/api/v1/runs/{rid}')).json()['status'] == 'completed'  # state stays consultable


async def test_events_ttl_index_follows_the_configured_retention(api):
    def ttl(info): return next(v['expireAfterSeconds'] for v in info.values() if v['key'] == [('occurred_at', 1)])
    assert ttl(await api.db.run_events.index_information()) == 7 * 86400
    await api.repo.ensure_indexes(3)
    assert ttl(await api.db.run_events.index_information()) == 3 * 86400


async def test_sse_sends_heartbeats_while_idle_and_ends_on_the_terminal_event(api):
    api.settings(SSE_HEARTBEAT_SECONDS='0.05')
    _, run = await new_run(api)

    async def finish_later():
        await asyncio.sleep(0.4)
        await api.repo.finish(DEMO, run['id'], 'completed', 'ok')
    task = asyncio.create_task(finish_later())
    response = await api.http.get(f"/api/v1/runs/{run['id']}/events")
    await task
    assert response.text.count(': heartbeat') >= 2
    frames = [f for f in parse_sse(response.text) if 'comment' not in f]
    assert [f['event'] for f in frames] == ['run.completed']


async def test_events_have_monotonic_unique_seq_under_concurrency(api):
    _, run = await new_run(api)
    await asyncio.gather(*[api.repo.append_event(DEMO, run['id'], 'tool.started', {'n': i}) for i in range(25)])
    events = await api.repo.events(DEMO, run['id'], 0, limit=100)
    assert [e['seq'] for e in events] == list(range(1, 26)) and (await api.repo.get_run(DEMO, run['id']))['seq'] == 25


# ---------- cancellation, lease, races ----------

async def test_explicit_cancellation_persists_partial_and_is_idempotent(api):
    started = await start_blocked_chat(api)
    rid = started['run_id']
    cancelled = (await api.http.post(f'/api/v1/runs/{rid}/cancel')).json()
    assert cancelled['status'] == 'cancelled' and cancelled['partial_text'] == 'Olá mundo' and cancelled['cancel_requested'] and not cancelled['final']
    again = (await api.http.post(f'/api/v1/runs/{rid}/cancel')).json()
    assert again['status'] == 'cancelled' and again['partial_text'] == 'Olá mundo'
    frames = parse_sse((await api.http.get(f'/api/v1/runs/{rid}/events')).text)
    assert frames[-1]['event'] == 'run.cancelled' and sum(f['event'].startswith('run.') and f['event'] != 'run.started' for f in frames) == 1
    assert ''.join(f['data']['payload']['text'] for f in frames if f['event'] == 'response.delta') == 'Olá mundo'
    messages = (await api.http.get(f"/api/v1/conversations/{started['conversation_id']}/messages")).json()['items']
    assert messages[-1]['status'] == 'cancelled' and messages[-1]['content'] == 'Olá mundo'
    assert 'active_run_id' not in await api.db.conversations.find_one({'id': started['conversation_id']})
    assert (await api.http.post('/api/v1/runs/nao-existe/cancel')).status_code == 404


async def test_cancellation_requested_in_the_database_is_noticed_by_the_executing_instance(api):
    started = await start_blocked_chat(api, RUN_HEARTBEAT_SECONDS='0.05')
    rid = started['run_id']
    first = (await api.repo.get_run(DEMO, rid))['lease_expires_at']
    await asyncio.sleep(0.25)
    assert (await api.repo.get_run(DEMO, rid))['lease_expires_at'] > first  # heartbeat renews the lease
    await api.repo.request_cancel(DEMO, rid)  # as if another instance had received POST /cancel
    run = await wait_status(api, rid, 'cancelled')
    assert run['partial_text'] == 'Olá mundo'


async def test_first_terminal_state_wins_the_cancel_completion_race(api):
    for i in range(5):
        _, run = await new_run(api, key=f'k{i}')
        results = await asyncio.gather(api.repo.finish(DEMO, run['id'], 'completed', 'resposta'),
                                       api.repo.finish(DEMO, run['id'], 'cancelled', 'parcial'))
        assert sorted(results) == [False, True]
        final = await api.repo.get_run(DEMO, run['id'])
        terminal = [e async for e in api.db.run_events.find({'run_id': run['id'], 'type': {'$regex': '^run\\.'}})]
        assert [e['type'] for e in terminal] == [f"run.{final['status']}"] and terminal[0]['seq'] == final['seq']
        assert await api.db.messages.count_documents({'run_id': run['id'], 'role': 'assistant'}) == 1


async def test_cancel_after_completion_reports_the_real_state(api):
    body, _ = await completed_run(api)
    response = (await api.http.post(f"/api/v1/runs/{body['run_id']}/cancel")).json()
    assert response['status'] == 'completed' and response['final'] and not response['cancel_requested']
    types = [e['type'] async for e in api.db.run_events.find({'run_id': body['run_id']})]
    assert 'run.cancelled' not in types


async def test_expired_lease_marks_run_interrupted_keeping_partial_and_never_regenerates(api):
    conversation_alive, alive = await new_run(api, key='alive')
    conversation, run = await new_run(api, key='dead')
    await api.repo.append_event(DEMO, run['id'], 'response.delta', {'text': 'parcial'})
    await api.db.runs.update_one({'id': run['id']}, {'$set': {'lease_expires_at': datetime(2000, 1, 1)}})
    assert await api.repo.interrupt_expired() == 1 and await api.repo.interrupt_expired() == 0
    dead = (await api.http.get(f"/api/v1/runs/{run['id']}")).json()
    assert dead['status'] == 'interrupted' and dead['partial_text'] == 'parcial' and dead['error']['code'] == 'interrupted' and not dead['final']
    assert (await api.http.get(f"/api/v1/runs/{alive['id']}")).json()['status'] == 'running'  # a live lease is untouched
    frames = parse_sse((await api.http.get(f"/api/v1/runs/{run['id']}/events")).text)  # stream ends on the terminal event
    assert [f['event'] for f in frames] == ['response.delta', 'run.interrupted'] and frames[-1]['data']['payload']['code'] == 'lease_expired'
    message = await api.db.messages.find_one({'run_id': run['id'], 'role': 'assistant'})
    assert message['status'] == 'interrupted' and message['content'] == 'parcial'
    assert 'active_run_id' not in await api.db.conversations.find_one({'id': conversation['id']})  # user may send again
    assert not await api.repo.claim_run(DEMO, run['id'], 'late', 30) and api.ctl.calls == 0
    again, created = await api.repo.create_run(DEMO, conversation['id'], 'nova mensagem', 'nova-chave')
    assert created and again['id'] != run['id']


# ---------- isolation ----------

async def test_two_users_and_two_workspaces_cannot_reach_each_others_runs(api):
    owner = Identity('u1', 'ws1', 'operator')
    others = [Identity('u2', 'ws1', 'operator'), Identity('u1', 'ws2', 'operator')]
    conversation, run = await new_run(api, who=owner)
    await api.repo.append_event(owner, run['id'], 'response.delta', {'text': 'segredo do u1'})
    await api.repo.save_evidence(owner, run['id'], [{'evidence_id': 1}], [], [])
    for other in others:
        assert await api.repo.get_conversation(other, conversation['id']) is None
        assert (await api.repo.list_conversations(other))[0] == [] and await api.repo.messages(other, conversation['id']) == []
        assert await api.repo.get_run(other, run['id']) is None and await api.repo.events(other, run['id'], 0) == []
        assert await api.repo.first_seq(other, run['id']) is None and await api.repo.get_evidence(other, run['id']) is None
        assert await api.repo.heartbeat(other, run['id'], 30) is None
        await api.repo.request_cancel(other, run['id'])
        assert not await api.repo.finish(other, run['id'], 'cancelled', 'x')
        with pytest.raises(ValueError):
            await api.repo.append_event(other, run['id'], 'tool.started', {})
        with pytest.raises(ConversationNotFound):
            await api.repo.create_run(other, conversation['id'], 'invasão', 'k')
    mine = await api.repo.get_run(owner, run['id'])
    assert mine['status'] == 'running' and not mine.get('cancel_requested') and mine['partial_text'] == 'segredo do u1'
    assert [e['payload']['text'] for e in await api.repo.events(owner, run['id'], 0)] == ['segredo do u1']


async def test_http_never_exposes_a_run_or_conversation_of_another_user(api):
    other = Identity('u2', 'demo', 'operator')
    conversation, run = await new_run(api, who=other)
    for method, url in [('get', f"/api/v1/runs/{run['id']}"), ('get', f"/api/v1/runs/{run['id']}/events"),
                        ('get', f"/api/v1/runs/{run['id']}/evidence"), ('post', f"/api/v1/runs/{run['id']}/cancel"),
                        ('get', f"/api/v1/conversations/{conversation['id']}/messages")]:
        response = await getattr(api.http, method)(url)
        assert response.status_code == 404 and 'u2' not in response.text, url
    assert (await api.http.get('/api/v1/conversations')).json()['items'] == []
    assert (await api.repo.get_run(other, run['id']))['status'] == 'running'  # the cancel attempt had no effect


async def test_support_data_is_isolated_by_user_and_workspace_and_carries_fixture_fields(api):
    base = {'sale_date': '2026-11-02', 'due_date': '2026-11-05', 'amount': '3482.90', 'status': 'previsto', 'payment_type': 'credito_a_vista'}
    await api.db.receivables.insert_many([{**base, 'id': 'a', 'workspace_id': 'ws1', 'user_id': 'u1'},
                                          {**base, 'id': 'b', 'workspace_id': 'ws1', 'user_id': 'u2', 'amount': '1.00'},
                                          {**base, 'id': 'c', 'workspace_id': 'ws2', 'user_id': 'u1', 'amount': '2.00'}])
    await api.db.terminals.insert_one({'id': 't', 'workspace_id': 'ws1', 'user_id': 'u1', 'model': 'Get Smart', 'connectivity': 'sem conexão',
                                       'error': 'Sem sinal da rede móvel', 'serial_suffix': '4821', 'carrier': 'Algar', 'carrier_signal': False})
    repo = MongoSupportRepository(api.db)
    mine = await repo.get_receivables(Identity('u1', 'ws1', 'operator'))
    assert [str(r.amount) for r in mine] == ['3482.90'] and mine[0].payment_type == 'credito_a_vista'
    assert [str(r.amount) for r in await repo.get_receivables(Identity('u2', 'ws1', 'operator'))] == ['1.00']
    assert [str(r.amount) for r in await repo.get_receivables(Identity('u1', 'ws2', 'operator'))] == ['2.00']
    assert await repo.get_receivables(Identity('u3', 'ws1', 'operator')) == []
    terminal = (await repo.get_terminals(Identity('u1', 'ws1', 'operator')))[0]
    assert (terminal.serial_suffix, terminal.carrier, terminal.carrier_signal) == ('4821', 'Algar', False) and await repo.get_terminals(Identity('u1', 'ws2', 'operator')) == []



# ---------- application lifespan ----------

async def test_lifespan_maintenance_interrupts_expired_runs_and_shutdown_interrupts_local_ones(monkeypatch):
    from app.adapters.inbound.http import main
    name = f'test_core_{uuid.uuid4().hex[:10]}'
    for key, value in {'MONGO_DATABASE': name, 'OPENAI_API_KEY': 'test-key', 'MAINTENANCE_INTERVAL_SECONDS': '0.05', 'CHAT_WAIT_SECONDS': '0.05'}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear(); get_run_settings.cache_clear()
    execution_state.blocked = asyncio.Event()
    control = Control()
    control.turns = [blocking]

    async def snapshot(db_, workspace_id): return 'g1', {'router': ROUTER}
    monkeypatch.setattr(execution, 'load_published_snapshot', snapshot)
    client = AsyncMongoClient(URI, serverSelectionTimeoutMS=2000)
    try:
        async with main.app.router.lifespan_context(main.app):
            main.app.state.runtime_factory = lambda settings, db_: Runtime(control, control, None, control.close)
            repo = MongoConversations(main.app.state.mongo[name])
            _, expired = await new_run_in(repo, 'expired')
            await repo.db.runs.update_one({'id': expired['id']}, {'$set': {'lease_expires_at': datetime(2000, 1, 1)}})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://t') as http:
                started = (await http.post('/api/v1/chat', json={'message': 'oi', 'user_id': 'cliente1988'})).json()
                await asyncio.wait_for(execution_state.blocked.wait(), 5)
                for _ in range(100):  # the maintenance task, not the request, performs this transition
                    if (await repo.get_run(DEMO, expired['id']))['status'] == 'interrupted': break
                    await asyncio.sleep(0.05)
                assert (await repo.get_run(DEMO, expired['id']))['status'] == 'interrupted'
                assert (await repo.get_run(DEMO, started['run_id']))['status'] == 'running'
        run = await MongoConversations(client[name]).get_run(DEMO, started['run_id'])  # lifespan exit cancelled the local task
        assert run['status'] == 'interrupted' and run['partial_text'] == 'Olá mundo' and not run.get('cancel_requested')
    finally:
        await client.drop_database(name)
        await client.close()
        get_settings.cache_clear(); get_run_settings.cache_clear()


async def new_run_in(repo, key):
    conversation = await repo.create_conversation(DEMO)
    run, _ = await repo.create_run(DEMO, conversation['id'], 'm', key, lease_seconds=60)
    await repo.claim_run(DEMO, run['id'], 'test', 60)
    return conversation, run
