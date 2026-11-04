"""Configuration and composition for the knowledge pipeline (worker, CLIs and HTTP share it)."""
from functools import lru_cache
from pathlib import Path
from openai import AsyncOpenAI
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.adapters.outbound.mongo.ingestion_jobs import MongoIngestionJobs
from app.adapters.outbound.mongo.knowledge import MongoKnowledgeRepository
from app.adapters.outbound.mongo.search_indexes import ensure_search_indexes
from app.adapters.outbound.openai.gateway import OpenAIModelGateway
from app.adapters.outbound.web.extract import extract_markdown
from app.adapters.outbound.web.fetch import SafeFetcher
from app.application.use_cases.ingest_sources import IngestionPipeline
from app.application.use_cases.knowledge_catalog import official_sources
from app.bootstrap.settings import Settings
from app.domain.knowledge import EMBEDDING_DIMENSIONS


class KnowledgeSettings(BaseSettings):
    """KNOWLEDGE_* environment variables; defaults suit a run from backend/."""
    model_config = SettingsConfigDict(env_file=('../.env', '.env'), env_prefix='KNOWLEDGE_', extra='ignore')
    data_dir: Path = Path('../data/knowledge')  # holds getnet/ (curated corpus) and sources.json
    chunk_max_chars: int = 1200
    chunk_overlap: int = 150
    fetch_max_bytes: int = 2_000_000
    fetch_timeout_s: float = 15.0
    fetch_max_redirects: int = 3
    embedding_dimensions: int | None = None  # required only for embedding models not in the built-in table
    job_lease_s: float = 60.0
    job_timeout_s: float = 600.0
    job_max_attempts: int = 3
    job_retry_delay_s: float = 30.0
    worker_poll_s: float = 2.0


@lru_cache
def get_knowledge_settings() -> KnowledgeSettings:
    return KnowledgeSettings()


def embedding_dimensions(settings: Settings, knowledge: KnowledgeSettings) -> int:
    dimensions = knowledge.embedding_dimensions or EMBEDDING_DIMENSIONS.get(settings.embedding_model)
    if not dimensions:
        raise SystemExit(f'Informe KNOWLEDGE_EMBEDDING_DIMENSIONS para o modelo {settings.embedding_model}.')
    return dimensions


def build_pipeline(settings: Settings, knowledge: KnowledgeSettings, db, openai_client: AsyncOpenAI | None,
                   transport=None) -> IngestionPipeline:
    hosts, _ = official_sources(knowledge.data_dir / 'sources.json')
    fetcher = SafeFetcher(hosts, transport=transport, max_bytes=knowledge.fetch_max_bytes,
                          timeout=knowledge.fetch_timeout_s, max_redirects=knowledge.fetch_max_redirects)
    return IngestionPipeline(
        repository=MongoKnowledgeRepository(db, settings.embedding_model),
        gateway=OpenAIModelGateway(openai_client) if openai_client else None,
        embedding_model=settings.embedding_model, curated_root=knowledge.data_dir / 'getnet', fetcher=fetcher,
        extract_html=extract_markdown, dimensions=embedding_dimensions(settings, knowledge),
        max_chars=knowledge.chunk_max_chars, overlap=knowledge.chunk_overlap)


async def prepare_storage(settings: Settings, knowledge: KnowledgeSettings, db) -> dict[str, str]:
    """Regular and Atlas Search indexes, idempotent; returns the search index status by name."""
    await MongoKnowledgeRepository(db, settings.embedding_model).ensure_indexes()
    await MongoIngestionJobs(db).ensure_indexes()
    return await ensure_search_indexes(db, embedding_dimensions(settings, knowledge))
