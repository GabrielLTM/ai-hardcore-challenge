"""Ingestion worker: registers the source catalog, then consumes the Mongo job queue until stopped."""
import asyncio
import logging
import signal
from uuid import uuid4
from openai import AsyncOpenAI
from pymongo import AsyncMongoClient
from app.adapters.inbound.knowledge_wiring import build_pipeline, get_knowledge_settings, prepare_storage
from app.adapters.outbound.mongo.ingestion_jobs import MongoIngestionJobs
from app.application.use_cases.ingestion_jobs import process_next
from app.application.use_cases.knowledge_catalog import load_catalog
from app.bootstrap.settings import get_settings

log = logging.getLogger('ingestion-worker')


async def main():
    settings, knowledge = get_settings(), get_knowledge_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    client = AsyncMongoClient(settings.mongo_uri)
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value()) if settings.openai_api_key else None
    try:
        db = client[settings.mongo_database]
        pipeline = build_pipeline(settings, knowledge, db, openai_client)
        try:
            log.warning('índices de busca: %s', await prepare_storage(settings, knowledge, db))
        except Exception:  # without Atlas Search the queue still works; retrieval needs the indexes
            log.exception('não foi possível preparar os índices; execute o comando ensure_indexes')
        jobs = MongoIngestionJobs(db)
        # shortcut: one shared catalog registered for the demo workspace only, per-workspace catalogs when tenants own sources
        await pipeline.repository.register_sources(settings.demo_workspace_id, list(load_catalog(knowledge.data_dir).values()))
        worker_id = f'worker-{uuid4().hex[:8]}'
        log.warning('worker %s aguardando jobs (embeddings %s)', worker_id, 'ativos' if openai_client else 'indisponíveis: sem OPENAI_API_KEY')
        while not stop.is_set():
            try:
                busy = await process_next(jobs, pipeline, lambda: load_catalog(knowledge.data_dir), worker_id,
                                          lease_s=knowledge.job_lease_s, timeout_s=knowledge.job_timeout_s,
                                          retry_delay_s=knowledge.job_retry_delay_s)
            except Exception:
                log.exception('falha ao consumir a fila; nova tentativa em breve')
                busy = False
            if not busy:
                try:
                    await asyncio.wait_for(stop.wait(), knowledge.worker_poll_s)
                except asyncio.TimeoutError:
                    pass
    finally:
        if openai_client:
            await openai_client.close()
        await client.close()


if __name__ == '__main__':
    asyncio.run(main())
