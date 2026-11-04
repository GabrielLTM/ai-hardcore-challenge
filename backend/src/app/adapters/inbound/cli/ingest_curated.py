"""Run with PYTHONPATH=src python -m app.adapters.inbound.cli.ingest_curated PATH."""
import argparse
import asyncio
from pathlib import Path
from openai import AsyncOpenAI
from pymongo import AsyncMongoClient
from app.adapters.inbound.knowledge_wiring import embedding_dimensions, get_knowledge_settings, prepare_storage
from app.adapters.outbound.mongo.knowledge import MongoKnowledgeRepository
from app.adapters.outbound.openai.gateway import OpenAIModelGateway
from app.application.use_cases.ingest_curated import ingest_curated
from app.bootstrap.settings import get_settings


async def run(path: Path) -> None:
    settings, knowledge = get_settings(), get_knowledge_settings()
    if not settings.openai_api_key:
        raise SystemExit('Configure OPENAI_API_KEY para gerar embeddings.')
    client = AsyncMongoClient(settings.mongo_uri)
    model = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    try:
        db = client[settings.mongo_database]
        await prepare_storage(settings, knowledge, db)
        count = await ingest_curated(path, OpenAIModelGateway(model), MongoKnowledgeRepository(db, settings.embedding_model),
                                     settings.embedding_model, settings.demo_workspace_id,
                                     max_chars=knowledge.chunk_max_chars, overlap=knowledge.chunk_overlap,
                                     dimensions=embedding_dimensions(settings, knowledge))
        print(f'{count} documentos processados.')
    finally:
        await model.close()
        await client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=Path, help='Diretório contendo manifest.json e documents/')
    args = parser.parse_args()
    asyncio.run(run(args.path))
