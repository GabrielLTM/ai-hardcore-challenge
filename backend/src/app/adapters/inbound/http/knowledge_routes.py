"""Knowledge base endpoints (sources, chunks, ingestion jobs). Admin only."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from app.adapters.inbound.http.errors import ApiError
from app.adapters.inbound.http.identity import Who, require_admin
from app.adapters.inbound.knowledge_wiring import get_knowledge_settings
from app.adapters.outbound.mongo.ingestion_jobs import MongoIngestionJobs
from app.adapters.outbound.mongo.knowledge import MongoKnowledgeRepository
from app.bootstrap.settings import get_settings
from app.domain.identity import Identity

router = APIRouter(prefix='/api/v1')
JOB_PRIVATE = ('workspace_id', 'lease_expires_at', 'heartbeat_at')


def admin(who: Who) -> Identity:
    return require_admin(who)


def database(request: Request):
    return request.app.state.mongo[get_settings().mongo_database]


def rfc3339(value):
    """Mongo returns naive UTC datetimes; the API always speaks RFC 3339 with a Z suffix."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    if isinstance(value, dict):
        return {k: rfc3339(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rfc3339(v) for v in value]
    return value


def source_view(doc: dict) -> dict:
    return {'id': doc['source_id'], **{k: v for k, v in doc.items() if k != 'source_id'}}


def job_view(job: dict) -> dict:
    return rfc3339({k: v for k, v in job.items() if k not in JOB_PRIVATE})


class IngestionInput(BaseModel):
    source_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)


@router.get('/knowledge/sources')
async def list_sources(request: Request, identity: Identity = Depends(admin)):
    docs = await MongoKnowledgeRepository(database(request)).list_sources(identity.workspace_id)
    return {'items': [rfc3339(source_view(doc)) for doc in docs]}


@router.get('/knowledge/sources/{source_id}')
async def get_source(source_id: str, request: Request, identity: Identity = Depends(admin)):
    repository = MongoKnowledgeRepository(database(request))
    doc = await repository.get_source(identity.workspace_id, source_id)
    if doc is None:
        raise ApiError(404, 'source_not_found', 'Fonte não encontrada.')
    return rfc3339({**source_view(doc), 'chunks': await repository.list_chunks(identity.workspace_id, source_id)})


@router.post('/knowledge/ingestions', status_code=202)
async def create_ingestion(request: Request, payload: IngestionInput | None = None, identity: Identity = Depends(admin)):
    db = database(request)
    known = await MongoKnowledgeRepository(db).source_ids(identity.workspace_id)
    if not known:
        raise ApiError(409, 'no_sources', 'Nenhuma fonte registrada. Inicie o worker de ingestão para registrar o catálogo.')
    requested = list(dict.fromkeys(payload.source_ids)) if payload and payload.source_ids else known
    unknown = [sid for sid in requested if sid not in known]
    if unknown:
        raise ApiError(422, 'unknown_source', 'Há fontes que não existem na base.', {'source_ids': unknown})
    job = await MongoIngestionJobs(db).create(identity.workspace_id, identity.user_id, requested,
                                              get_knowledge_settings().job_max_attempts)
    return job_view(job)


@router.get('/knowledge/ingestions')
async def list_ingestions(request: Request, limit: int = Query(20, ge=1, le=100), identity: Identity = Depends(admin)):
    jobs = await MongoIngestionJobs(database(request)).recent(identity.workspace_id, limit)
    return {'items': [job_view(job) for job in jobs]}


@router.get('/knowledge/ingestions/{job_id}')
async def get_ingestion(job_id: str, request: Request, identity: Identity = Depends(admin)):
    job = await MongoIngestionJobs(database(request)).get(identity.workspace_id, job_id)
    if job is None:
        raise ApiError(404, 'ingestion_not_found', 'Ingestão não encontrada.')
    return job_view(job)
