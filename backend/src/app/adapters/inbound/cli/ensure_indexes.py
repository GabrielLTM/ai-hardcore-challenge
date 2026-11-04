"""Create the knowledge indexes (idempotent): PYTHONPATH=src python -m app.adapters.inbound.cli.ensure_indexes [--wait SECONDS]."""
import argparse
import asyncio
from pymongo import AsyncMongoClient
from app.adapters.inbound.knowledge_wiring import get_knowledge_settings, prepare_storage
from app.adapters.outbound.mongo.search_indexes import wait_queryable
from app.bootstrap.settings import get_settings


async def run(wait: float) -> None:
    settings = get_settings()
    client = AsyncMongoClient(settings.mongo_uri)
    try:
        db = client[settings.mongo_database]
        for name, status in (await prepare_storage(settings, get_knowledge_settings(), db)).items():
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
