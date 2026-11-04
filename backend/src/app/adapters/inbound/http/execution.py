"""Background execution of a run on this instance: wiring, event recording, lease heartbeat and maintenance."""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import uuid4
from openai import AsyncOpenAI
from opentelemetry import trace
from app.adapters.inbound.http.run_settings import RunSettings, get_run_settings
from app.adapters.outbound.fake.model import FakeModel
from app.adapters.outbound.mongo.agents import load_published_snapshot
from app.adapters.outbound.mongo.conversations import MongoConversations
from app.adapters.outbound.mongo.handoffs import MongoHandoffRepository
from app.adapters.outbound.mongo.knowledge import MongoKnowledgeRepository
from app.adapters.outbound.mongo.support import MongoSupportRepository
from app.adapters.outbound.openai.gateway import OpenAIModelGateway
from app.adapters.outbound.openai.web import OpenAIWebSearch
from app.application.use_cases.evidence import event_view
from app.application.use_cases.orchestrator import ExecutionError, Orchestrator
from app.application.use_cases.support import SupportService
from app.application.use_cases.tools import make_tools
from app.bootstrap.settings import get_settings
from app.bootstrap.telemetry import traced_emit
from app.domain.identity import Identity

log = logging.getLogger(__name__)
INSTANCE_ID = uuid4().hex  # identifies this API process as lease owner
GENERIC_FAILURE = 'Não foi possível concluir o atendimento.'


@dataclass(slots=True)
class Runtime:
    """External collaborators of a run; tests replace the factory on `app.state.runtime_factory`."""
    gateway: Any
    knowledge: Any
    web_search: Any
    close: Callable[[], Awaitable[None]]


def default_runtime(settings, db) -> Runtime:
    if settings.model_provider == 'fake':  # load tests: no OpenAI client, no key needed
        fake = FakeModel()
        return Runtime(fake, fake, None, fake.close)
    client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    return Runtime(OpenAIModelGateway(client), MongoKnowledgeRepository(db, settings.embedding_model), OpenAIWebSearch(client, settings.openai_model), client.close)


def current_trace_id() -> str:
    """Correlation id of a run, returned to the client and kept on the run for support: the active trace when there is one."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, '032x') if ctx.is_valid else uuid4().hex


class Recorder:
    """Persists a run's events in order; response deltas are batched to keep the number of writes low."""

    def __init__(self, repo: MongoConversations, who: Identity, run_id: str, batch_chars: int = 200, batch_seconds: float = 0.25):
        self.repo, self.who, self.run_id = repo, who, run_id
        self.batch_chars, self.batch_seconds = batch_chars, batch_seconds
        self.text: list[str] = []  # all streamed text (the partial answer)
        self._pending: list[str] = []
        self._agent_id = None
        self._flushed_at = time.monotonic()
        self._usage = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'cost_usd': 0.0}
        self._cost_known = True

    async def flush(self):
        if self._pending:
            text, self._pending = ''.join(self._pending), []
            await self.repo.append_event(self.who, self.run_id, 'response.delta', {'text': text, 'agent_id': self._agent_id})
        self._flushed_at = time.monotonic()

    async def __call__(self, kind: str, data: dict):
        if kind == 'response.delta':
            self.text.append(data['text'])
            self._pending.append(data['text'])
            self._agent_id = data.get('agent_id')
            if sum(map(len, self._pending)) >= self.batch_chars or time.monotonic() - self._flushed_at >= self.batch_seconds:
                await self.flush()
            return
        await self.flush()
        await self.repo.append_event(self.who, self.run_id, kind, data)
        if kind == 'usage.recorded':
            await self._record_usage(data)
        elif kind == 'run.started' and data.get('trace_id'):
            await self.repo.set_fields(self.who, self.run_id, {'trace_id': data['trace_id']})

    async def _record_usage(self, data: dict):
        usage, cost = data.get('usage') or {}, data.get('cost_usd')
        self._usage['input_tokens'] += usage.get('input_tokens') or usage.get('prompt_tokens') or 0  # embeddings report prompt_tokens
        for key in ('output_tokens', 'total_tokens'):
            self._usage[key] += usage.get(key) or 0
        self._cost_known &= cost is not None
        self._usage['cost_usd'] += cost or 0
        total = {**self._usage, 'cost_usd': round(self._usage['cost_usd'], 6) if self._cost_known else None,
                 'pricing_version': data.get('pricing_version')}  # unknown cost stays null, never 0
        await self.repo.set_fields(self.who, self.run_id, {'usage': total})


async def _heartbeat(repo: MongoConversations, who: Identity, run_id: str, cfg: RunSettings, target: dict):
    """Renew the lease and notice cancellation requested through the database (e.g. by another instance)."""
    while True:
        await asyncio.sleep(cfg.run_heartbeat_seconds)
        try:
            run = await repo.heartbeat(who, run_id, cfg.run_lease_seconds)
        except Exception:  # transient outage: the lease is still valid for a while
            continue
        if run is None or run.get('cancel_requested'):  # finished elsewhere or cancel requested
            target['task'].cancel()
            return


# shortcut: fixed window of recent turns; summarise older ones if conversations get long enough to matter for tokens.
HISTORY_MESSAGES, HISTORY_CHARS = 10, 4000


async def conversation_history(repo: MongoConversations, who: Identity, run_id: str) -> list[dict[str, str]]:
    """Earlier user/assistant turns of the run's conversation, oldest first, without the message being answered."""
    run = await repo.get_run(who, run_id)
    rows = await repo.messages(who, run['conversation_id']) if run else []
    prior = [m for m in rows if m.get('run_id') != run_id and m.get('role') in ('user', 'assistant') and m.get('content')]
    return [{'role': m['role'], 'content': m['content'][:HISTORY_CHARS]} for m in prior[-HISTORY_MESSAGES:]]


async def execute_run(app, run_id: str, who: Identity, message: str):
    settings, cfg = get_settings(), get_run_settings()
    db = app.state.mongo[settings.mongo_database]
    repo = MongoConversations(db)
    runtime: Runtime = getattr(app.state, 'runtime_factory', default_runtime)(settings, db)
    recorder = Recorder(repo, who, run_id)
    emit = traced_emit(recorder, run_id)  # spans/metrics per event; the wrapper adds the real trace_id to run.started
    orchestrator = heartbeat = None
    target = {'task': asyncio.current_task()}
    outcome = ('failed', None, {'code': 'internal_error', 'message': GENERIC_FAILURE})
    try:
        try:
            if not await repo.claim_run(who, run_id, INSTANCE_ID, cfg.run_lease_seconds):
                getattr(emit, 'finish', lambda _: None)('cancelled')
                return  # already finished (e.g. cancelled before it started)
            heartbeat = asyncio.create_task(_heartbeat(repo, who, run_id, cfg, target))
            graph_version, agents = await load_published_snapshot(db, who.workspace_id)  # snapshot fixed for the whole run
            await repo.set_fields(who, run_id, {'graph_version': graph_version})
            conversation_id = ((await repo.get_run(who, run_id)) or {}).get('conversation_id')
            tools = make_tools(SupportService(MongoSupportRepository(db)), runtime.knowledge, runtime.gateway,
                               settings.embedding_model, runtime.web_search, MongoHandoffRepository(db), run_id, conversation_id)
            orchestrator = Orchestrator(runtime.gateway, agents, tools, cfg.limits())
            await emit('run.started', {'graph_version': graph_version})
            history = await conversation_history(repo, who, run_id)
            target['task'] = asyncio.create_task(orchestrator.run(message, who, emit, history))  # cancellable apart from the bookkeeping below
            outcome = ('completed', await target['task'], None)
        except asyncio.CancelledError:
            run = await repo.get_run(who, run_id)
            cancelled = bool(run and run.get('cancel_requested'))  # otherwise: shutdown or lost lease
            outcome = ('cancelled' if cancelled else 'interrupted', None,
                       None if cancelled else {'code': 'interrupted', 'message': 'A execução foi interrompida antes de concluir.'})
        except ExecutionError as exc:
            outcome = ('failed', None, {'code': exc.code, 'message': str(exc)})
        except Exception:
            log.exception('Falha inesperada na execução %s', run_id)
        finally:
            if heartbeat: heartbeat.cancel()
        try:
            await _settle(repo, who, run_id, recorder, orchestrator, emit, *outcome)
        except Exception:
            log.exception('Falha ao registrar o estado final da execução %s', run_id)  # lease expiry will mark it interrupted
        getattr(emit, 'finish', lambda _: None)(outcome[0])  # terminal events are persisted by _settle, not emitted
    finally:
        await runtime.close()


async def _settle(repo, who, run_id, recorder: Recorder, orchestrator, emit, status: str, answer: str | None, error: dict | None):
    await recorder.flush()
    text = answer if answer is not None else ''.join(recorder.text)
    cited, invalid = orchestrator.evidence.resolve(text) if orchestrator else ([], [])
    if orchestrator:
        await repo.save_evidence(who, run_id, orchestrator.evidence.retrieved, [i['evidence_id'] for i in cited], invalid)
    payload = {'cited_evidence': [i['evidence_id'] for i in cited], 'invalid_citations': invalid, **(error or {})}
    await repo.finish(who, run_id, status, text, payload=payload, sources=[event_view(i) for i in cited], error=error)


def launch_run(app, run_id: str, who: Identity, message: str) -> asyncio.Task:
    task = asyncio.create_task(execute_run(app, run_id, who, message))
    app.state.tasks[run_id] = task
    task.add_done_callback(lambda _: app.state.tasks.pop(run_id, None))
    return task


async def maintenance_loop(repo: MongoConversations, cfg: RunSettings):
    """Periodically mark runs whose lease expired as interrupted (their instance crashed or stalled)."""
    while True:
        try:
            await repo.interrupt_expired()
        except Exception:
            log.warning('Manutenção de execuções falhou; tentará novamente.')
        await asyncio.sleep(cfg.maintenance_interval_seconds)
