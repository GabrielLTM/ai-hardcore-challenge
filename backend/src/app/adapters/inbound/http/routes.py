import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import uuid4
from fastapi import APIRouter, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from app.adapters.inbound.http.errors import ApiError
from app.adapters.inbound.http.execution import execute_run, current_trace_id, launch_run  # noqa: F401 (execute_run re-exported)
from app.adapters.inbound.http.identity import Who, bind_user
from app.adapters.inbound.http.run_settings import get_run_settings
from app.adapters.outbound.mongo.conversations import ACTIVE, TERMINAL, MongoConversations, scope
from app.bootstrap.settings import get_settings

router = APIRouter(prefix='/api/v1')

_CURSOR = re.compile(r'\d{1,18}')
_RUN_FIELDS = ('id', 'conversation_id', 'status', 'graph_version', 'trace_id', 'partial_text', 'sources', 'usage', 'error',
               'cancel_requested', 'seq', 'created_at', 'updated_at')


class RunInput(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    user_id: str = Field(min_length=1, max_length=200)


class ChatInput(RunInput):
    conversation_id: str | None = Field(default=None, max_length=64)  # omitted: a new conversation per request


class ConversationInput(BaseModel):
    title: str = Field(default='Nova conversa', min_length=1, max_length=200)


def repository(request: Request):
    settings = get_settings()
    return MongoConversations(request.app.state.mongo[settings.mongo_database])


def _rfc3339(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def jsonable(data: Any):
    """JSON-ready copy with every datetime as UTC RFC 3339."""
    return jsonable_encoder(data, custom_encoder={datetime: _rfc3339})


def run_view(run: dict) -> dict:
    view = {key: run.get(key) for key in _RUN_FIELDS}
    return jsonable({**view, 'run_id': run['id'], 'sources': view['sources'] or [], 'final': run['status'] == 'completed',
                     'cancel_requested': bool(run.get('cancel_requested')), 'events_url': f"/api/v1/runs/{run['id']}/events"})


async def get_run_or_404(repo: MongoConversations, who, run_id: str) -> dict:
    run = await repo.get_run(who, run_id)
    if not run: raise ApiError(404, 'run_not_found', 'Execução não encontrada.')
    return run


@router.post('/conversations', status_code=201)
async def create_conversation(payload: ConversationInput, request: Request, who: Who):
    return jsonable(await repository(request).create_conversation(who, payload.title))


@router.get('/conversations')
async def list_conversations(request: Request, who: Who, q: Annotated[str | None, Query(max_length=200)] = None,
                             limit: Annotated[int, Query(ge=1, le=100)] = 30, cursor: str | None = None):
    try:
        items, next_cursor = await repository(request).list_conversations(who, q=(q or '').strip() or None, limit=limit, cursor=cursor)
    except ValueError:
        raise ApiError(400, 'invalid_cursor', 'Cursor de paginação inválido.') from None
    return jsonable({'items': items, 'next_cursor': next_cursor})


@router.get('/conversations/{conversation_id}/messages')
async def messages(conversation_id: str, request: Request, who: Who):
    repo = repository(request)
    if not await repo.get_conversation(who, conversation_id): raise ApiError(404, 'conversation_not_found', 'Conversa não encontrada.')
    return jsonable({'items': await repo.messages(who, conversation_id)})


@router.get('/runs/{run_id}')
async def get_run(run_id: str, request: Request, who: Who):
    return run_view(await get_run_or_404(repository(request), who, run_id))


@router.get('/runs/{run_id}/evidence')
async def evidence(run_id: str, request: Request, who: Who):
    repo = repository(request)
    run = await get_run_or_404(repo, who, run_id)
    doc = await repo.get_evidence(who, run_id) or {}
    retrieved = doc.get('retrieved', [])
    return jsonable({'run_id': run_id, 'status': run['status'], 'retrieved': retrieved,
                     'cited': [item for item in retrieved if item['cited']], 'invalid_citations': doc.get('invalid_citations', [])})


async def begin_run(request: Request, who, message: str, conversation_id: str, key: str) -> dict:
    """Create the run (or replay the idempotent one) and start it on this instance."""
    settings, cfg = get_settings(), get_run_settings()
    run, created = await repository(request).create_run(who, conversation_id, message, key, lease_seconds=cfg.run_lease_seconds,
                                                        max_active=cfg.max_active_runs, trace_id=current_trace_id())
    if created: launch_run(request.app, run['id'], who, message)
    return run


def require_provider():
    settings = get_settings()
    if settings.model_provider == 'fake':  # load tests run without a provider key
        return
    key = settings.openai_api_key
    if not key or not key.get_secret_value():
        raise ApiError(503, 'provider_not_configured', 'Configure OPENAI_API_KEY para iniciar o atendimento.')


@router.post('/conversations/{conversation_id}/runs', status_code=202)
async def start_run(conversation_id: str, payload: RunInput, request: Request, who: Who,
                    idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=200)]):
    bind_user(who, payload.user_id)
    require_provider()
    run = await begin_run(request, who, payload.message, conversation_id, idempotency_key)
    return {'conversation_id': conversation_id, 'run_id': run['id'], 'status': run['status'],
            'events_url': f"/api/v1/runs/{run['id']}/events"}


@router.post('/chat')
async def chat(payload: ChatInput, request: Request, who: Who):
    """Challenge-compatible endpoint: waits for the run up to CHAT_WAIT_SECONDS, then answers 202 with the ids to poll."""
    cfg = get_run_settings()
    bind_user(who, payload.user_id)
    require_provider()
    repo = repository(request)
    conversation_id = payload.conversation_id
    created_here = conversation_id is None
    if created_here:
        conversation_id = (await repo.create_conversation(who, payload.message[:60]))['id']
    try:
        run = await begin_run(request, who, payload.message, conversation_id, uuid4().hex)
    except Exception:
        if created_here: await repo.db.conversations.delete_one({**scope(who), 'id': conversation_id})  # no empty orphan
        raise
    task = request.app.state.tasks.get(run['id'])
    if task: await asyncio.wait({task}, timeout=cfg.chat_wait_seconds)  # on timeout the run keeps going
    run = await get_run_or_404(repo, who, run['id'])
    ids = {'conversation_id': conversation_id, 'run_id': run['id'], 'trace_id': run.get('trace_id')}
    if run['status'] in ACTIVE:
        return JSONResponse({**ids, 'answer': None, 'sources': [], 'status': run['status'], 'events_url': f"/api/v1/runs/{run['id']}/events",
                             'run_url': f"/api/v1/runs/{run['id']}"}, status_code=202)
    if run['status'] == 'failed':
        raise ApiError(502, 'run_failed', (run.get('error') or {}).get('message') or 'Não foi possível concluir o atendimento.',
                       details={**ids, 'error_code': (run.get('error') or {}).get('code')})
    return jsonable({**ids, 'answer': run['partial_text'], 'sources': run.get('sources') or [], 'status': run['status']})


def parse_cursor(after: str | None, last_event_id: str | None) -> int:
    raw = last_event_id if last_event_id else after  # EventSource reconnects with Last-Event-ID but keeps the original URL
    if raw is None or raw == '': return 0
    if not _CURSOR.fullmatch(raw): raise ApiError(400, 'invalid_cursor', 'Cursor inválido: informe o último seq recebido.')
    return int(raw)


def sse_frame(event: dict) -> str:
    payload = event['payload']
    data = {'run_id': event['run_id'], 'seq': event['seq'], 'occurred_at': event['occurred_at'], 'type': event['type'],
            **({'agent_id': payload['agent_id']} if payload.get('agent_id') else {}), 'payload': payload}
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(jsonable(data), ensure_ascii=False)}\n\n"


@router.get('/runs/{run_id}/events')
async def events(run_id: str, request: Request, who: Who, after: str | None = None,
                 last_event_id: Annotated[str | None, Header(alias='Last-Event-ID')] = None):
    repo, cfg = repository(request), get_run_settings()
    cursor = parse_cursor(after, last_event_id)
    run = await get_run_or_404(repo, who, run_id)
    if cursor > run['seq']:
        raise ApiError(400, 'invalid_cursor', 'Cursor à frente do último evento da execução.')
    first = await repo.first_seq(who, run_id)
    if cursor < run['seq'] and (first is None or cursor < first - 1):  # retention removed events the client has not seen
        raise ApiError(410, 'cursor_expired', 'Os eventos desse cursor expiraram; consulte a execução e o histórico da conversa.',
                       details={'run_id': run_id, 'first_available_seq': first})

    async def generate():
        position, last_write = cursor, time.monotonic()
        while not await request.is_disconnected():
            rows = await repo.events(who, run_id, position)
            for event in rows:
                position = event['seq']
                yield sse_frame(event)
            if rows: last_write = time.monotonic()
            current = await repo.get_run(who, run_id)
            if not current or (current['status'] in TERMINAL and position >= current['seq']): break
            if not rows:
                if time.monotonic() - last_write >= cfg.sse_heartbeat_seconds:
                    yield ': heartbeat\n\n'
                    last_write = time.monotonic()
                await asyncio.sleep(cfg.sse_poll_seconds)
    return StreamingResponse(generate(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.post('/runs/{run_id}/cancel')
async def cancel(run_id: str, request: Request, who: Who):
    """Idempotent; answers with the real state, which may still be running if the executing instance has not yet noticed."""
    repo, cfg = repository(request), get_run_settings()
    run = await get_run_or_404(repo, who, run_id)
    if run['status'] in ACTIVE:
        await repo.request_cancel(who, run_id)
        task = request.app.state.tasks.get(run_id)
        if task:  # executing here: stop it now; elsewhere its heartbeat picks the request up
            task.cancel()
            await asyncio.wait({task}, timeout=cfg.cancel_wait_seconds)
            if task.done() and (await repo.get_run(who, run_id))['status'] in ACTIVE:  # cancelled before it ever started
                await repo.finish(who, run_id, 'cancelled', None)
        run = await get_run_or_404(repo, who, run_id)
    return run_view(run)
