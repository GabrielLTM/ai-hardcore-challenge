"""Collect, chunk, embed and index the official URLs.

PYTHONPATH=src python -m app.adapters.inbound.cli.ingest_urls [--source ID ...]
Creates a job in the shared queue and works the queue until that job finishes (a running worker may take it).
"""
import argparse
import asyncio
from uuid import uuid4
from openai import AsyncOpenAI
from pymongo import AsyncMongoClient
from app.adapters.inbound.knowledge_wiring import build_pipeline, get_knowledge_settings, prepare_storage
from app.adapters.outbound.mongo.ingestion_jobs import MongoIngestionJobs
from app.application.use_cases.ingestion_jobs import process_next
from app.application.use_cases.knowledge_catalog import load_catalog
from app.bootstrap.settings import get_settings
from app.domain.knowledge import OFFICIAL

TERMINAL = ('succeeded', 'partial', 'failed')


async def run(source_ids: list[str] | None) -> int:
    settings, knowledge = get_settings(), get_knowledge_settings()
    if not settings.openai_api_key:
        raise SystemExit('Configure OPENAI_API_KEY para gerar embeddings.')
    catalog = load_catalog(knowledge.data_dir)
    official = [sid for sid, s in catalog.items() if s['document_type'] == OFFICIAL]
    unknown = [sid for sid in source_ids or [] if sid not in catalog]
    if unknown:
        raise SystemExit(f'Fonte desconhecida: {", ".join(unknown)}')
    client = AsyncMongoClient(settings.mongo_uri)
    model = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    try:
        db = client[settings.mongo_database]
        pipeline = build_pipeline(settings, knowledge, db, model)
        await prepare_storage(settings, knowledge, db)
        await pipeline.repository.register_sources(settings.demo_workspace_id, list(catalog.values()))
        jobs = MongoIngestionJobs(db)
        job = await jobs.create(settings.demo_workspace_id, 'cli', source_ids or official, knowledge.job_max_attempts)
        worker_id = f'cli-{uuid4().hex[:8]}'
        while job['status'] not in TERMINAL:
            busy = await process_next(jobs, pipeline, lambda: catalog, worker_id, lease_s=knowledge.job_lease_s,
                                      timeout_s=knowledge.job_timeout_s, retry_delay_s=knowledge.job_retry_delay_s)
            job = await jobs.get(settings.demo_workspace_id, job['id'])
            if not busy and job['status'] not in TERMINAL:
                await asyncio.sleep(1)
        for result in job['results']:
            detail = (f"falhou em {result['error']['phase']}: {result['error']['message']}" if result['outcome'] == 'failed'
                      else f"{result['outcome']} ({result['chunks']} trechos)" if 'chunks' in result else result['outcome'])
            print(f"{result['source_id']}: {detail}")
        if job['error'] and not job['results']:
            print(f"falhou em {job['error']['phase']}: {job['error']['message']}")
        print(f"job {job['id']}: {job['status']}")
        return 0 if job['status'] == 'succeeded' else 1
    finally:
        await model.close()
        await client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', action='append', help='ID da fonte; repita para várias (padrão: todas as URLs oficiais).')
    raise SystemExit(asyncio.run(run(parser.parse_args().source)))
