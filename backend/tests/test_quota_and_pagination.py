"""9.3 atomic active-run cap across instances, and conversation keyset pagination (local Mongo; skipped when down)."""
import asyncio
import uuid
import pytest
from pymongo import AsyncMongoClient, MongoClient
from app.adapters.outbound.mongo.conversations import MongoConversations
from app.domain.errors import CapacityExceeded
from app.domain.identity import Identity

URI = 'mongodb://localhost:27017/?directConnection=true'


def _mongo_up() -> bool:
    try:
        MongoClient(URI, serverSelectionTimeoutMS=1000).admin.command('ping')
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mongo_up(), reason='Mongo local indisponível')
WHO = Identity('u1', 'ws-quota', 'operator')


@pytest.fixture
async def dbs():
    name = f'test_quota_{uuid.uuid4().hex[:8]}'
    a, b = AsyncMongoClient(URI), AsyncMongoClient(URI)  # two clients: two API instances
    await MongoConversations(a[name]).ensure_indexes()
    yield a[name], b[name]
    await a.drop_database(name)
    await a.close(); await b.close()


async def test_concurrent_runs_never_exceed_the_workspace_cap(dbs):
    one, two = MongoConversations(dbs[0]), MongoConversations(dbs[1])
    conversations = [await (one if i % 2 else two).create_conversation(WHO, f'c{i}') for i in range(12)]

    async def start(i, conversation):
        repo = one if i % 2 else two
        try:
            await repo.create_run(WHO, conversation['id'], 'oi', f'k{i}', max_active=5)
            return 'ok'
        except CapacityExceeded:
            return 'full'

    results = await asyncio.gather(*(start(i, c) for i, c in enumerate(conversations)))
    assert results.count('ok') == 5 and results.count('full') == 7
    assert await dbs[0].runs.count_documents({'workspace_id': WHO.workspace_id, 'status': {'$in': ['queued', 'running']}}) == 5


async def test_conversations_page_by_cursor_and_search(dbs):
    repo = MongoConversations(dbs[0])
    for i in range(5):
        await repo.create_conversation(WHO, f'Pix {i}' if i % 2 else f'Maquininha {i}')
    first, cursor = await repo.list_conversations(WHO, limit=2)
    second, cursor2 = await repo.list_conversations(WHO, limit=2, cursor=cursor)
    third, cursor3 = await repo.list_conversations(WHO, limit=2, cursor=cursor2)
    ids = [c['id'] for c in first + second + third]
    assert len(ids) == len(set(ids)) == 5 and cursor3 is None
    found, _ = await repo.list_conversations(WHO, q='pix')
    assert sorted(c['title'] for c in found) == ['Pix 1', 'Pix 3']
    with pytest.raises(ValueError):
        await repo.list_conversations(WHO, cursor='não-é-cursor')
