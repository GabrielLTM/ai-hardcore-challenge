"""Apply every index the application needs, idempotently, as a deploy step (run it before rolling out a new version):

    PYTHONPATH=src python -m app.adapters.inbound.cli.apply_indexes [--wait SECONDS]

Covers conversations/runs/events (with the event TTL), agents and graph versions, knowledge, ingestion jobs, the
Atlas Search/Vector indexes and the optional history retention (HISTORY_RETENTION_DAYS). Additive: it never drops data.
"""
import argparse
import asyncio
from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure
from app.adapters.inbound.http.run_settings import RunSettings, get_run_settings
from app.adapters.inbound.knowledge_wiring import KnowledgeSettings, get_knowledge_settings, prepare_storage
from app.adapters.outbound.mongo.agents import MongoAgentRepository
from app.adapters.outbound.mongo.handoffs import MongoHandoffRepository
from app.adapters.outbound.mongo.conversations import MongoConversations
from app.adapters.outbound.mongo.search_indexes import wait_queryable
from app.bootstrap.settings import Settings, get_settings

# collection -> date field its retention clock follows
HISTORY = {'conversations': 'updated_at', 'messages': 'created_at', 'runs': 'created_at', 'run_evidence': 'updated_at'}


async def apply_history_retention(db, days: int | None) -> dict[str, str]:
    """Declarative: with `days` TTL indexes exist with that age; with None they are removed (nothing is deleted any more)."""
    status = {}
    for collection, field in HISTORY.items():
        name, existing = f'{collection}_retention_ttl', await db[collection].index_information()
        if days is None:
            if name in existing:
                await db[collection].drop_index(name)
            status[name] = 'removed' if name in existing else 'off'
            continue
        ttl = days * 86400
        try:
            await db[collection].create_index(field, expireAfterSeconds=ttl, name=name)
        except OperationFailure:  # same index, other age: change it in place
            await db.command('collMod', collection, index={'name': name, 'expireAfterSeconds': ttl})
        status[name] = f'{days}d'
    return status


async def apply_regular_indexes(db, settings: Settings, run_settings: RunSettings) -> dict[str, str]:
    await MongoConversations(db).ensure_indexes(run_settings.event_retention_days)
    await MongoAgentRepository(db).ensure_indexes()
    await MongoHandoffRepository(db).ensure_indexes()
    return {'run_events_ttl': f'{run_settings.event_retention_days}d', **await apply_history_retention(db, settings.history_retention_days)}


async def apply_all(db, settings: Settings, run_settings: RunSettings, knowledge: KnowledgeSettings) -> dict[str, str]:
    return {**await apply_regular_indexes(db, settings, run_settings), **await prepare_storage(settings, knowledge, db)}


async def run(wait: float) -> None:
    settings = get_settings()
    client = AsyncMongoClient(settings.mongo_uri)
    try:
        db = client[settings.mongo_database]
        for name, status in (await apply_all(db, settings, get_run_settings(), get_knowledge_settings())).items():
            print(f'{name}: {status}')
        if wait:
            ready = await wait_queryable(db, wait)
            print('índices prontos para consulta.' if ready else f'índices ainda em construção após {wait:.0f}s.')
            raise SystemExit(0 if ready else 1)
    finally:
        await client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--wait', type=float, default=0, help='Aguarda até N segundos pelos índices de busca.')
    asyncio.run(run(parser.parse_args().wait))
