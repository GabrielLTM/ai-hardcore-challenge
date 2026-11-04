"""Integration tests against the local Atlas Local replica set; skipped when it does not answer."""
import asyncio
import os
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import OperationFailure
from app.adapters.inbound.http import knowledge_routes
from app.adapters.inbound.http.errors import ApiError
from app.adapters.outbound.mongo.ingestion_jobs import MongoIngestionJobs
from app.adapters.outbound.mongo.knowledge import MongoKnowledgeRepository
from app.adapters.outbound.mongo.search_indexes import ensure_search_indexes, wait_queryable
from app.application.ports.knowledge import LeaseLost
from app.application.use_cases.ingestion_jobs import process_next
from app.bootstrap.settings import Settings
from app.domain.identity import Identity
from knowledge_doubles import LONG, official, page, pipeline, site

URI = 'mongodb://localhost:27017/?directConnection=true'
WS = 'demo'
SOURCE = official()
CATALOG = {SOURCE['id']: SOURCE}
PAGE = page(('Antecipação', LONG), ('Pix', LONG + 'Pix é instantâneo.'))


UNAVAILABLE = []  # remembers why Mongo cannot be used so the other tests skip without waiting for the timeout again


@pytest_asyncio.fixture
async def client():
    if UNAVAILABLE:
        pytest.skip(UNAVAILABLE[0])
    mongo = AsyncMongoClient(URI, serverSelectionTimeoutMS=1500)
    try:
        hello = await mongo.admin.command('hello')
        if 'setName' not in hello:
            UNAVAILABLE.append('Mongo local sem replica set (transações indisponíveis)')
    except Exception:
        UNAVAILABLE.append('Mongo local indisponível')
    if UNAVAILABLE:
        await mongo.close()
        pytest.skip(UNAVAILABLE[0])
    yield mongo
    await mongo.close()


@pytest_asyncio.fixture
async def db(client):
    name = f'test_knowledge_{uuid4().hex[:10]}'
    yield client[name]
    await client.drop_database(name)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakePipeline:
    def __init__(self, results=None, error=None, delay=0):
        self.results, self.error, self.delay, self.cancelled = results, error, delay, False

    async def run(self, workspace_id, job_id, catalog, source_ids, report):
        try:
            await report('collect', 'running')
            await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            await report('collect', 'succeeded', processed=len(source_ids), failed=0)
            return self.results if self.results is not None else [{'source_id': s, 'outcome': 'updated'} for s in source_ids]
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def failed(source_id, phase='collect'):
    return {'source_id': source_id, 'outcome': 'failed', 'error': {'phase': phase, 'code': 'unreachable', 'message': 'x'}}


# --- queue: lease, heartbeat, retries ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_claim_is_exclusive_and_oldest_first(db):
    jobs = MongoIngestionJobs(db)
    first, second = await jobs.create(WS, 'u', ['a']), await jobs.create(WS, 'u', ['b'])
    assert (await jobs.claim('A', 30))['id'] == first['id']
    claimed = await jobs.claim('B', 30)
    assert claimed['id'] == second['id'] and claimed['attempts'] == 1 and 'lease_owner' not in claimed
    assert await jobs.claim('C', 30) is None


@pytest.mark.asyncio
async def test_concurrent_claims_never_hand_out_a_job_twice(db):
    jobs = MongoIngestionJobs(db)
    created = {(await jobs.create(WS, 'u', [str(i)]))['id'] for i in range(5)}
    claimed = await asyncio.gather(*(jobs.claim(f'w{i}', 30) for i in range(12)))
    ids = [job['id'] for job in claimed if job]
    assert len(ids) == 5 and set(ids) == created


@pytest.mark.asyncio
async def test_worker_death_lets_another_worker_take_over_after_the_lease_expires(db):
    clock = Clock()
    jobs = MongoIngestionJobs(db, clock)
    job = await jobs.create(WS, 'u', ['a'])
    await jobs.claim('A', 30)
    await jobs.set_phase(job['id'], 'A', 'collect', 'running')
    clock.advance(20)
    assert await jobs.heartbeat(job['id'], 'A', 30) and await jobs.claim('B', 30) is None  # heartbeat renewed the lease
    clock.advance(31)  # A dies: no more heartbeats
    taken = await jobs.claim('B', 30)
    assert taken['id'] == job['id'] and taken['attempts'] == 2 and taken['phases']['collect'] == {'status': 'pending'}
    assert not await jobs.heartbeat(job['id'], 'A', 30)
    with pytest.raises(LeaseLost):
        await jobs.set_phase(job['id'], 'A', 'collect', 'succeeded')
    with pytest.raises(LeaseLost):
        await jobs.finish(job['id'], 'A', 'succeeded', [])
    await jobs.finish(job['id'], 'B', 'succeeded', [{'source_id': 'a', 'outcome': 'updated'}])
    stored = await jobs.get(WS, job['id'])
    assert stored['status'] == 'succeeded' and stored['results'][0]['outcome'] == 'updated'


@pytest.mark.asyncio
async def test_expired_lease_without_attempts_left_fails_reporting_the_phase(db):
    clock = Clock()
    jobs = MongoIngestionJobs(db, clock)
    job = await jobs.create(WS, 'u', ['a'], max_attempts=1)
    await jobs.claim('A', 30)
    await jobs.set_phase(job['id'], 'A', 'extract', 'running')
    clock.advance(31)
    assert await jobs.claim('B', 30) is None
    stored = await jobs.get(WS, job['id'])
    assert stored['status'] == 'failed' and stored['error']['code'] == 'lease_expired' and stored['error']['phase'] == 'extract'


@pytest.mark.asyncio
async def test_retry_is_limited_and_backs_off(db):
    clock = Clock()
    jobs = MongoIngestionJobs(db, clock)
    job = await jobs.create(WS, 'u', ['a'], max_attempts=2)
    await jobs.claim('A', 30)
    await jobs.set_phase(job['id'], 'A', 'embed', 'running')
    await jobs.retry_or_fail(job['id'], 'A', 'internal', 'falhou', delay_s=10)
    assert (await jobs.get(WS, job['id']))['status'] == 'queued' and await jobs.claim('B', 30) is None
    clock.advance(10)
    assert (await jobs.claim('B', 30))['attempts'] == 2
    await jobs.retry_or_fail(job['id'], 'B', 'internal', 'falhou de novo', delay_s=10)
    stored = await jobs.get(WS, job['id'])
    assert stored['status'] == 'failed' and stored['error']['code'] == 'internal' and stored['attempts'] == 2


# --- process_next -------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_next_records_outcome_by_source(db):
    jobs = MongoIngestionJobs(db)
    ok, partial, bad = [await jobs.create(WS, 'u', ids) for ids in (['a'], ['a', 'b'], ['c'])]
    pipes = iter([FakePipeline(), FakePipeline([{'source_id': 'a', 'outcome': 'already_current'}, failed('b', 'embed')]),
                  FakePipeline([failed('c')])])
    for job in (ok, partial, bad):
        assert await process_next(jobs, next(pipes), lambda: CATALOG, 'w')
    assert not await process_next(jobs, FakePipeline(), lambda: CATALOG, 'w')
    statuses = [(await jobs.get(WS, j['id'])) for j in (ok, partial, bad)]
    assert [s['status'] for s in statuses] == ['succeeded', 'partial', 'failed']
    assert statuses[0]['phases']['collect']['status'] == 'succeeded' and statuses[0]['finished_at']
    assert statuses[1]['error'] == {'source_id': 'b', 'phase': 'embed', 'code': 'unreachable', 'message': 'x'}


@pytest.mark.asyncio
async def test_process_next_retries_exceptions_and_timeouts_without_leaking_messages(db):
    jobs = MongoIngestionJobs(db)
    job = await jobs.create(WS, 'u', ['a'], max_attempts=3)
    await process_next(jobs, FakePipeline(error=RuntimeError('senha=abc')), lambda: CATALOG, 'w', retry_delay_s=0)
    stored = await jobs.get(WS, job['id'])
    assert stored['status'] == 'queued' and stored['error']['code'] == 'internal' and stored['error']['phase'] == 'collect'
    assert 'senha' not in stored['error']['message'] and 'RuntimeError' in stored['error']['message']
    await process_next(jobs, FakePipeline(delay=5), lambda: CATALOG, 'w', timeout_s=0.05, retry_delay_s=0)
    stored = await jobs.get(WS, job['id'])
    assert stored['status'] == 'queued' and stored['error']['code'] == 'timeout' and stored['attempts'] == 2
    await process_next(jobs, FakePipeline(), lambda: CATALOG, 'w')
    assert (await jobs.get(WS, job['id']))['status'] == 'succeeded'


@pytest.mark.asyncio
async def test_broken_catalog_goes_through_the_retry_path(db):
    jobs = MongoIngestionJobs(db)
    job = await jobs.create(WS, 'u', ['a'])

    def broken():
        raise ValueError('sources.json inválido')

    await process_next(jobs, FakePipeline(), broken, 'w', retry_delay_s=0)
    stored = await jobs.get(WS, job['id'])
    assert stored['status'] == 'queued' and stored['error']['code'] == 'internal' and 'ValueError' in stored['error']['message']


@pytest.mark.asyncio
async def test_job_orphaned_by_a_dead_worker_is_finished_by_the_next_one(db):
    clock = Clock()
    jobs = MongoIngestionJobs(db, clock)
    job = await jobs.create(WS, 'u', ['a'])
    await jobs.claim('dead-worker', 30)
    assert not await process_next(jobs, FakePipeline(), lambda: CATALOG, 'B', lease_s=30)
    clock.advance(31)
    assert await process_next(jobs, FakePipeline(), lambda: CATALOG, 'B', lease_s=30)
    stored = await jobs.get(WS, job['id'])
    assert stored['status'] == 'succeeded' and stored['attempts'] == 2


@pytest.mark.asyncio
async def test_worker_that_loses_its_lease_stops_and_does_not_overwrite_the_new_owner(db):
    jobs = MongoIngestionJobs(db)
    job = await jobs.create(WS, 'u', ['a'])
    pipe = FakePipeline(delay=5)

    async def steal():
        await asyncio.sleep(0.05)
        await db.ingestion_jobs.update_one({'id': job['id']}, {'$set': {'lease_owner': 'other'}})

    thief = asyncio.ensure_future(steal())
    await asyncio.wait_for(process_next(jobs, pipe, lambda: CATALOG, 'slow', lease_s=0.3), 3)
    await thief
    stored = await jobs.get(WS, job['id'])
    assert pipe.cancelled and stored['status'] == 'running' and stored['results'] == []


# --- repository: atomic replacement ------------------------------------------------------------------

async def ingest(repo, jobs, fetcher, **kwargs):
    job = await jobs.create(WS, 'u', [SOURCE['id']])
    await process_next(jobs, pipeline(repo, fetcher, **kwargs), lambda: CATALOG, 'w')
    return await jobs.get(WS, job['id'])


@pytest.mark.asyncio
async def test_ingestion_is_idempotent_versioned_and_keeps_the_last_valid_version_on_failure(db):
    repo, jobs = MongoKnowledgeRepository(db, 'embedding'), MongoIngestionJobs(db)
    await repo.ensure_indexes()
    await repo.register_sources(WS, [SOURCE])
    assert (await repo.get_source(WS, SOURCE['id']))['status'] == 'pending'

    first = await ingest(repo, jobs, site(PAGE))
    assert first['status'] == 'succeeded' and first['results'][0]['outcome'] == 'updated'
    assert all(first['phases'][p]['status'] == 'succeeded' for p in ('collect', 'extract', 'chunk', 'embed', 'index'))
    assert await db.knowledge_chunks.count_documents({}) == 2
    version = (await repo.get_source(WS, SOURCE['id']))['index_version']

    again = await ingest(repo, jobs, site(PAGE))
    assert again['results'][0]['outcome'] == 'already_current' and again['phases']['embed']['status'] == 'skipped'
    source = await repo.get_source(WS, SOURCE['id'])
    assert await db.knowledge_chunks.count_documents({}) == 2 and len(source['versions']) == 1 and source['index_version'] == version

    changed = page(('Antecipação', LONG + 'Condição nova.'))
    await ingest(repo, jobs, site(changed))
    source = await repo.get_source(WS, SOURCE['id'])
    chunks = await repo.list_chunks(WS, SOURCE['id'])
    assert [c['chunk_id'] for c in chunks] == ['official-x#0'] and 'Condição nova.' in chunks[0]['text']  # stale chunk removed
    assert len(source['versions']) == 2 and source['index_version'] != version
    good_version = source['index_version']

    down = await ingest(repo, jobs, site(lambda r: httpx.Response(503, headers={'content-type': 'text/html'})))
    source = await repo.get_source(WS, SOURCE['id'])
    assert down['status'] == 'failed' and down['error'] == {
        'source_id': SOURCE['id'], 'phase': 'collect', 'code': 'unreachable', 'message': 'A fonte respondeu HTTP 503.'}
    assert source['status'] == 'stale' and source['index_version'] == good_version
    assert source['last_attempt']['outcome'] == 'failed' and source['last_attempt']['error']['phase'] == 'collect'
    assert [c['chunk_id'] for c in await repo.list_chunks(WS, SOURCE['id'])] == ['official-x#0']

    await ingest(repo, jobs, site(changed))
    assert (await repo.get_source(WS, SOURCE['id']))['status'] == 'indexed'


@pytest.mark.asyncio
async def test_failure_while_writing_the_index_rolls_back_to_the_previous_version(db, monkeypatch):
    repo, jobs = MongoKnowledgeRepository(db, 'embedding'), MongoIngestionJobs(db)
    await repo.register_sources(WS, [SOURCE])
    await ingest(repo, jobs, site(PAGE))
    before = (await repo.get_source(WS, SOURCE['id']))['index_version']
    original = AsyncCollection.update_one

    async def flaky(self, *args, **kwargs):
        if self.name == 'knowledge_sources' and kwargs.get('session') is not None:
            raise OperationFailure('falha simulada no meio da troca')
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(AsyncCollection, 'update_one', flaky)
    job = await ingest(repo, jobs, site(page(('Antecipação', LONG + 'Outra versão.'))))
    assert job['results'][0]['error']['phase'] == 'index' and job['results'][0]['error']['code'] == 'index_failed'
    chunks = await repo.list_chunks(WS, SOURCE['id'])
    assert len(chunks) == 2 and 'Outra versão.' not in ''.join(c['text'] for c in chunks)
    source = await repo.get_source(WS, SOURCE['id'])
    assert source['index_version'] == before and source['status'] == 'stale'


@pytest.mark.asyncio
async def test_ensure_indexes_replaces_the_legacy_one_document_per_source_index(db):
    await db.knowledge_chunks.create_index([('workspace_id', 1), ('source_id', 1)], unique=True)
    await db.knowledge_chunks.insert_one({'workspace_id': WS, 'source_id': 'old', 'text': 'um documento por fonte'})
    repo = MongoKnowledgeRepository(db, 'embedding')
    await repo.ensure_indexes()
    await repo.ensure_indexes()
    assert await db.knowledge_chunks.count_documents({}) == 0  # legacy documents have no chunk_id and are rebuilt by ingestion
    await db.knowledge_chunks.insert_many([{'workspace_id': WS, 'source_id': 's', 'chunk_id': f's#{i}'} for i in range(2)])
    with pytest.raises(Exception):
        await db.knowledge_chunks.insert_one({'workspace_id': WS, 'source_id': 's', 'chunk_id': 's#0'})


# --- Atlas Search / Vector Search --------------------------------------------------------------------

def chunk_doc(source_id, index, heading, text, vector):
    return {'chunk_id': f'{source_id}#{index}', 'source_id': source_id, 'chunk_index': index, 'title': heading,
            'heading': heading, 'text': text, 'embedding': vector, 'embedding_model': 'embedding',
            'index_version': 'v1', 'document_type': 'official_page', 'source_url': f'https://x/{source_id}',
            'retrieved_on': '2026-11-03', 'limitations': ['L']}


async def seed(repo, workspace, source_id, docs):
    record = {'document_type': 'official_page', 'title': source_id, 'source_url': f'https://x/{source_id}', 'publisher': 'G',
              'region': 'BR', 'language': 'pt-BR', 'tags': [], 'limitations': [], 'retrieved_on': '2026-11-03',
              'content_sha256': 'h', 'processing_version': 'p', 'index_version': 'v1', 'embedding_model': 'embedding',
              'chunk_count': len(docs)}
    await repo.replace_source(workspace, source_id, record, docs)


async def search_until(repo, workspace, query, vector, predicate, timeout=60):
    """Atlas Local indexes asynchronously: poll until the results satisfy the predicate or the timeout passes."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        results = await repo.hybrid_search(workspace, query, vector, limit=5)
        if predicate(results) or asyncio.get_running_loop().time() > deadline:
            return results
        await asyncio.sleep(1)


@pytest.mark.asyncio
async def test_hybrid_search_with_real_indexes_is_scoped_and_traceable(db):
    repo = MongoKnowledgeRepository(db, 'embedding')
    await repo.ensure_indexes()
    try:
        assert await ensure_search_indexes(db, 3) == {'knowledge_text': 'created', 'knowledge_vector': 'created'}
    except OperationFailure:
        pytest.skip('Atlas Search indisponível neste Mongo')
    assert await ensure_search_indexes(db, 3) == {'knowledge_text': 'exists', 'knowledge_vector': 'exists'}
    with pytest.raises(RuntimeError, match='dimensões'):
        await ensure_search_indexes(db, 1536)
    assert await wait_queryable(db, 90)

    await seed(repo, 'w1', 'smart', [chunk_doc('smart', 0, 'Maquininha Get Smart', 'A Get Smart roda aplicativos e automação.', [1, 0, 0])])
    await seed(repo, 'w1', 'pix', [chunk_doc('pix', 0, 'Pix', 'Recebimento instantâneo por Pix no POS Digital.', [0, 1, 0])])
    await seed(repo, 'w2', 'other', [chunk_doc('other', 0, 'Get Smart do outro workspace', 'Conteúdo privado do workspace dois.', [1, 0, 0])])

    results = await search_until(repo, 'w1', 'Get Smart', [0.9, 0.1, 0], lambda r: len(r) == 2)
    assert [r['chunk_id'] for r in results] == ['smart#0', 'pix#0']  # both signals agree on smart; pix only semantic-near
    assert results[0]['score'] > results[1]['score']
    contract = {'chunk_id', 'source_id', 'title', 'text', 'source_url', 'retrieved_on', 'limitations', 'document_type', 'index_version'}
    assert contract <= results[0].keys() and 'embedding' not in results[0] and '_id' not in results[0]

    lexical_only = await search_until(repo, 'w1', 'Pix', [0, 0, 1], lambda r: any(d['chunk_id'] == 'pix#0' for d in r))
    assert 'pix#0' in [r['chunk_id'] for r in lexical_only]  # text signal alone retrieves the product by name

    assert {r['chunk_id'] for r in await repo.hybrid_search('w2', 'Get Smart', [1, 0, 0])} == {'other#0'}
    assert await repo.hybrid_search('w3', 'Get Smart', [1, 0, 0]) == []
    assert await MongoKnowledgeRepository(db, 'outro-modelo').hybrid_search('w1', 'Get Smart', [1, 0, 0]) == []

    await seed(repo, 'w1', 'smart', [])  # replacing a source with fewer chunks removes the old ones from search
    gone = await search_until(repo, 'w1', 'Get Smart', [1, 0, 0], lambda r: all(d['chunk_id'] != 'smart#0' for d in r))
    assert all(d['chunk_id'] != 'smart#0' for d in gone)


# --- HTTP ---------------------------------------------------------------------------------------------

@pytest_asyncio.fixture
async def api(client, db, monkeypatch):
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    app.state.mongo = client

    @app.exception_handler(ApiError)
    async def handle(request, exc):
        return JSONResponse({'code': exc.code, 'message': exc.message, 'details': exc.details}, status_code=exc.status)

    monkeypatch.setattr(knowledge_routes, 'get_settings', lambda: Settings(mongo_database=db.name))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
        http.app = app
        yield http


@pytest.mark.asyncio
async def test_endpoints_expose_sources_chunks_versions_and_ingestion_phases(api, db):
    assert (await api.get('/api/v1/knowledge/sources')).json() == {'items': []}
    assert (await api.post('/api/v1/knowledge/ingestions')).json()['code'] == 'no_sources'

    repo, jobs = MongoKnowledgeRepository(db, 'embedding'), MongoIngestionJobs(db)
    await repo.register_sources(WS, [SOURCE, official('official-y', 'https://site.getnet.com.br/y/')])
    listed = (await api.get('/api/v1/knowledge/sources')).json()['items']
    assert [(s['id'], s['status'], s['document_type']) for s in listed] == [('official-x', 'pending', 'official_page'),
                                                                            ('official-y', 'pending', 'official_page')]

    created = await api.post('/api/v1/knowledge/ingestions', json={'source_ids': ['official-x', 'official-x']})
    body = created.json()
    assert created.status_code == 202 and body['status'] == 'queued' and body['source_ids'] == ['official-x']
    assert body['created_at'].endswith('Z') and [p for p in body['phases']] == ['collect', 'extract', 'chunk', 'embed', 'index']
    assert 'workspace_id' not in body
    everything = (await api.post('/api/v1/knowledge/ingestions')).json()
    assert everything['source_ids'] == ['official-x', 'official-y']
    unknown = await api.post('/api/v1/knowledge/ingestions', json={'source_ids': ['nope']})
    assert unknown.status_code == 422 and unknown.json()['code'] == 'unknown_source' and unknown.json()['details'] == {'source_ids': ['nope']}
    assert (await api.post('/api/v1/knowledge/ingestions', json={'source_ids': []})).status_code == 422

    await db.ingestion_jobs.delete_one({'id': everything['id']})
    await process_next(jobs, pipeline(repo, site(PAGE)), lambda: CATALOG, 'w')
    job = (await api.get(f"/api/v1/knowledge/ingestions/{body['id']}")).json()
    assert job['status'] == 'succeeded' and job['results'][0]['outcome'] == 'updated'
    assert job['phases']['index']['status'] == 'succeeded' and job['phases']['index']['processed'] == 1
    assert job['finished_at'].endswith('Z') and 'lease_owner' not in job
    recent = (await api.get('/api/v1/knowledge/ingestions?limit=5')).json()['items']
    assert [j['id'] for j in recent] == [body['id']]

    detail = (await api.get('/api/v1/knowledge/sources/official-x')).json()
    assert detail['id'] == 'official-x' and detail['status'] == 'indexed' and detail['limitations'] == ['Limite editorial.']
    assert detail['last_indexed_at'].endswith('Z') and detail['source_url'] == SOURCE['source_url']
    assert [c['chunk_id'] for c in detail['chunks']] == ['official-x#0', 'official-x#1'] and 'embedding' not in detail['chunks'][0]
    assert detail['versions'][0]['index_version'] == detail['index_version'] and detail['last_attempt']['outcome'] == 'updated'
    assert 'versions' not in (await api.get('/api/v1/knowledge/sources')).json()['items'][0]

    assert (await api.get('/api/v1/knowledge/sources/missing')).json()['code'] == 'source_not_found'
    assert (await api.get('/api/v1/knowledge/ingestions/missing')).status_code == 404


@pytest.mark.asyncio
async def test_endpoints_require_an_administrator(api):
    api.app.dependency_overrides[knowledge_routes.admin] = lambda: knowledge_routes.require_admin(Identity('u', WS, 'operator'))
    for method, path in (('get', '/api/v1/knowledge/sources'), ('get', '/api/v1/knowledge/sources/x'),
                         ('post', '/api/v1/knowledge/ingestions'), ('get', '/api/v1/knowledge/ingestions'),
                         ('get', '/api/v1/knowledge/ingestions/x')):
        response = await getattr(api, method)(path)
        assert response.status_code == 403 and response.json()['code'] == 'forbidden'


# --- processes: CLI and worker ------------------------------------------------------------------------

BACKEND = Path(__file__).parents[1]


def child_env(db, **extra):
    # An empty key wins over any .env file: these processes must never reach the real OpenAI API.
    return {**os.environ, 'PYTHONPATH': 'src', 'MONGO_DATABASE': db.name, 'OPENAI_API_KEY': '', **extra}


@pytest.mark.asyncio
async def test_ensure_indexes_cli_is_idempotent(db):
    async def run(*args):
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'app.adapters.inbound.cli.ensure_indexes', *args, cwd=BACKEND, env=child_env(db),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(process.communicate(), 120)
        return process.returncode, out.decode(), err.decode()

    code, out, err = await run('--wait', '90')
    if 'OperationFailure' in err:
        pytest.skip('Atlas Search indisponível neste Mongo')
    assert code == 0, err
    assert 'knowledge_text: created' in out and 'knowledge_vector: created' in out and 'prontos' in out
    code, out, err = await run()
    assert code == 0 and 'knowledge_text: exists' in out and 'knowledge_vector: exists' in out
    vector = [i async for i in await db.knowledge_chunks.list_search_indexes(name='knowledge_vector')][0]
    assert vector['latestDefinition']['fields'][0]['numDimensions'] == 1536  # text-embedding-3-small


@pytest.mark.asyncio
async def test_worker_registers_the_catalog_consumes_the_queue_and_stops_on_sigterm(db):
    job = await MongoIngestionJobs(db).create(WS, 'test', ['01-visao-global'])  # curated: needs no network
    worker = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'app.adapters.inbound.worker', cwd=BACKEND,
        env=child_env(db, KNOWLEDGE_WORKER_POLL_S='0.1'), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        stored = None
        for _ in range(150):
            stored = await MongoIngestionJobs(db).get(WS, job['id'])
            if stored['status'] in ('succeeded', 'partial', 'failed'):
                break
            await asyncio.sleep(0.2)
    finally:
        worker.send_signal(signal.SIGTERM)
        _, err = await asyncio.wait_for(worker.communicate(), 30)
    assert worker.returncode == 0, err.decode()
    assert stored['status'] == 'failed' and stored['error']['phase'] == 'embed' and stored['error']['code'] == 'embedding_unavailable'
    assert [stored['phases'][p]['status'] for p in ('collect', 'extract', 'chunk', 'embed', 'index')] == [
        'succeeded', 'succeeded', 'succeeded', 'failed', 'skipped']
    repo = MongoKnowledgeRepository(db, 'text-embedding-3-small')
    assert len(await repo.source_ids(WS)) == 20  # 10 curated + 10 official URLs registered as pending
    assert (await repo.get_source(WS, '01-visao-global'))['status'] == 'failed'
    assert await db.knowledge_chunks.count_documents({}) == 0
