"""The idempotent index command: every collection gets its indexes, reruns change nothing, retention follows the setting."""
import uuid
import pytest
from pymongo import AsyncMongoClient, MongoClient
from app.adapters.inbound.cli.apply_indexes import HISTORY, apply_regular_indexes
from app.adapters.inbound.http.run_settings import RunSettings
from app.bootstrap.settings import Settings

URI = 'mongodb://localhost:27017/?directConnection=true'


def _mongo_up() -> bool:
    try:
        MongoClient(URI, serverSelectionTimeoutMS=1000).admin.command('ping')
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mongo_up(), reason='Mongo local indisponível')


@pytest.fixture
async def db():
    client = AsyncMongoClient(URI, serverSelectionTimeoutMS=2000)
    name = f'test_prod_idx_{uuid.uuid4().hex[:10]}'
    yield client[name]
    await client.drop_database(name)
    await client.close()


async def indexes(db):
    return {c: await db[c].index_information() for c in ('conversations', 'messages', 'runs', 'run_events', 'run_evidence', 'agents',
                                                          'agent_versions', 'prompt_versions', 'graph_versions', 'audit_events')}


def settings(days=None): return Settings(history_retention_days=days, _env_file=None)
def run_settings(days=7): return RunSettings(event_retention_days=days, _env_file=None)


async def test_all_indexes_are_created_and_reruns_are_no_ops(db):
    status = await apply_regular_indexes(db, settings(), run_settings())
    first = await indexes(db)
    assert all(len(info) > 1 for info in first.values())  # more than the default _id index everywhere
    assert first['run_events']['run_events_ttl']['expireAfterSeconds'] == 7 * 86400
    assert first['agent_versions'] and any(i.get('unique') for i in first['runs'].values())
    assert status['run_events_ttl'] == '7d' and all(v == 'off' for k, v in status.items() if k.endswith('_retention_ttl'))
    assert await apply_regular_indexes(db, settings(), run_settings()) == status
    assert await indexes(db) == first  # idempotent


async def test_event_retention_change_updates_the_ttl_in_place(db):
    await apply_regular_indexes(db, settings(), run_settings(7))
    await apply_regular_indexes(db, settings(), run_settings(3))
    assert (await db.run_events.index_information())['run_events_ttl']['expireAfterSeconds'] == 3 * 86400


async def test_history_retention_is_optional_and_reversible(db):
    await apply_regular_indexes(db, settings(30), run_settings())
    for collection in HISTORY:
        assert (await db[collection].index_information())[f'{collection}_retention_ttl']['expireAfterSeconds'] == 30 * 86400
    await apply_regular_indexes(db, settings(10), run_settings())  # changing the age is in place
    assert (await db.messages.index_information())['messages_retention_ttl']['expireAfterSeconds'] == 10 * 86400
    status = await apply_regular_indexes(db, settings(None), run_settings())  # unset: retention is switched off, data is kept
    for collection in HISTORY:
        assert f'{collection}_retention_ttl' not in await db[collection].index_information()
    assert {status[f'{c}_retention_ttl'] for c in HISTORY} == {'removed'}
