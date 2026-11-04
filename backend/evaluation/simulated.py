"""Offline harness: a scripted model, retrieval and web search around the REAL orchestrator, tools, evidence ledger and API.

The scripted model decides like the real one would (delegations, tool calls, answer text built from what the tools returned),
so the tests and the simulated evaluation exercise everything except the provider. It needs the local Mongo.
"""
import asyncio
import json
import os
import re
import sys
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
import httpx
from pymongo import AsyncMongoClient, MongoClient

_SRC = str(Path(__file__).resolve().parents[1] / 'src')
if _SRC not in sys.path:
    sys.path.append(_SRC)  # `python -m evaluation.run_eval` works without PYTHONPATH

from fastapi import FastAPI  # noqa: E402
from app.adapters.inbound.http import routes  # noqa: E402
from app.adapters.inbound.http.errors import register_error_handlers  # noqa: E402
from app.adapters.inbound.http.execution import Runtime  # noqa: E402
from app.adapters.inbound.http.run_settings import get_run_settings  # noqa: E402
from app.adapters.outbound.mongo.conversations import MongoConversations  # noqa: E402
from app.adapters.outbound.mongo.seed import seed_demo  # noqa: E402
from app.application.use_cases.knowledge_catalog import read_curated_corpus  # noqa: E402
from app.bootstrap.settings import get_settings  # noqa: E402
from app.domain.models import ModelEvent  # noqa: E402

CORPUS_ROOT = Path(__file__).resolve().parents[2] / 'data' / 'knowledge' / 'getnet'
MONGO_URI = os.environ.get('MONGO_URI') or 'mongodb://localhost:27017/?directConnection=true'
_FRAME = re.compile(r'<dados_nao_confiaveis origem="([^"]*)">\n(.*)\n</dados_nao_confiaveis>', re.S)


class SimulationUnavailable(RuntimeError):
    """The simulation needs a service (the local Mongo) that is not answering."""


def mongo_available() -> bool:
    try:
        MongoClient(MONGO_URI, serverSelectionTimeoutMS=1000).admin.command('ping')
        return True
    except Exception:
        return False


# ---------- what the scripted model reads and decides ----------

class Seen:
    """What an agent has in front of it on this turn: its task and the tool/delegation outputs received so far."""

    def __init__(self, inputs: list[dict]):
        self.task = inputs[0]['content']
        self.outputs = []
        for item in inputs:
            if item.get('type') == 'function_call_output':
                origin, body = _FRAME.fullmatch(item['output']).groups()
                self.outputs.append((origin, json.loads(body.replace('<\\/dados_nao_confiaveis', '</dados_nao_confiaveis'))))

    def last(self, origin: str) -> Any:
        return next(payload for name, payload in reversed(self.outputs) if name == origin)

    def passage(self, source_id: str) -> dict:
        found = next((r for r in self.last('search_knowledge')['results'] if r['source_id'] == source_id), None)
        if found is None:
            raise AssertionError(f'a busca não devolveu a fonte {source_id}')  # a scripted model cannot cite what it did not receive
        return found

    def cite(self, source_id: str) -> str:
        return self.passage(source_id)['cite_as']

    def collected(self, source_id: str) -> str:
        return date.fromisoformat(self.passage(source_id)['retrieved_on']).strftime('%d/%m/%Y')


@dataclass(slots=True)
class Call:
    name: str
    args: dict


Turn = Callable[[Seen], 'Call | str']  # a function call or the answer text


def call(name: str, **args) -> Turn:
    return lambda seen: Call(name, args)


def delegate(agent_id: str, task: str | Callable[[Seen], str]) -> Turn:
    return lambda seen: Call('delegate_to_agent', {'agent_id': agent_id, 'task': task(seen) if callable(task) else task})


def say(text: str | Callable[[Seen], str]) -> Turn:
    return lambda seen: text(seen) if callable(text) else text


@dataclass(slots=True)
class Scenario:
    """Turns per agent role, the passages the search returns (curated source ids or literal chunks) and the web result."""
    router: list[Turn] = field(default_factory=list)
    support: list[Turn] = field(default_factory=list)
    knowledge: list[Turn] = field(default_factory=list)
    escalation: list[Turn] = field(default_factory=list)
    passages: tuple[str | dict, ...] = ()
    web: dict | None = None
    search_fails: bool = False


def role_of(tools: list[dict]) -> str:
    names = {t['name'] for t in tools}
    return ('escalation' if 'request_human_handoff' in names else 'support' if 'get_receivables' in names
            else 'knowledge' if 'search_knowledge' in names else 'router')


def _pieces(text: str, size: int = 12) -> list[str]:
    words = text.split(' ')
    return [' '.join(words[i:i + size]) + (' ' if i + size < len(words) else '') for i in range(0, len(words), size)]


class ScriptedGateway:
    """ModelGateway double. Token counts are synthetic (characters / 4): they exercise accounting, not a provider bill."""

    def __init__(self, scenario: Scenario):
        self.turns = {role: list(getattr(scenario, role)) for role in ('router', 'support', 'knowledge', 'escalation')}
        self.spoke: list[str] = []  # role of each model call, in order
        self.transcript: list[str] = []  # everything the model was shown (the inputs of each call, as JSON)
        self.errors: list[str] = []

    def pending(self) -> dict[str, int]:
        """Scripted turns that never ran: non-empty means the run did not follow the script."""
        return {role: len(turns) for role, turns in self.turns.items() if turns}

    async def stream(self, *, model, instructions, inputs, tools, **extra):
        role = role_of(tools)
        try:
            decision = self.turns[role].pop(0)(Seen(inputs))
        except Exception as exc:  # a broken script must be visible, not a silent generic failure
            self.errors.append(f'{role}: {type(exc).__name__}: {exc}')
            raise
        self.spoke.append(role)
        shown = json.dumps(inputs, default=str, ensure_ascii=False)
        self.transcript.append(shown)
        tokens_in = (len(instructions) + len(shown)) // 4
        if isinstance(decision, Call):
            data = {'type': 'function_call', 'call_id': f'call_{len(self.spoke)}', 'name': decision.name, 'arguments': json.dumps(decision.args)}
            yield ModelEvent('function_call', data=data)
            output, tokens_out = [data], len(data['arguments']) // 4 + 5
        else:
            for piece in _pieces(decision):
                yield ModelEvent('text_delta', text=piece)
            output, tokens_out = [], max(1, len(decision) // 4)
        yield ModelEvent('completed', data={'output': output, 'usage': {
            'input_tokens': tokens_in, 'output_tokens': tokens_out, 'total_tokens': tokens_in + tokens_out}})

    async def embed(self, *, model, texts):
        return [[0.1] for _ in texts]


def corpus_chunks(root: Path = CORPUS_ROOT) -> dict[str, dict]:
    """The curated documents as retrieval results, one chunk per document (as the corpus README suggests)."""
    return {d['id']: {'chunk_id': f"{d['id']}#0", 'source_id': d['id'], 'title': d['title'], 'heading': d['title'], 'text': d['text'],
                      'source_url': d['source_url'], 'retrieved_on': d['retrieved_on'], 'limitations': d['limitations'],
                      'document_type': d['document_type'], 'index_version': 'simulated'} for d in read_curated_corpus(root)}


class ScriptedKnowledge:
    def __init__(self, corpus: dict[str, dict], passages: tuple[str | dict, ...], fails: bool = False):
        self.corpus, self.passages, self.fails, self.queries = corpus, passages, fails, []

    async def hybrid_search(self, workspace_id, query, embedding, limit=5):
        self.queries.append((workspace_id, query))
        if self.fails:
            raise ConnectionError('busca indisponível (simulado)')
        return [dict(self.corpus[p]) if isinstance(p, str) else dict(p) for p in self.passages]


class ScriptedWeb:
    def __init__(self, result: dict | None):
        self.result, self.queries = result, []

    async def __call__(self, query: str) -> dict:
        self.queries.append(query)
        if self.result is None:
            raise AssertionError('web_search chamada sem resultado roteirizado')
        return {**self.result, 'retrieved_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}


# ---------- in-process app ----------

@dataclass
class Simulation:
    http: httpx.AsyncClient
    app: FastAPI
    db: Any
    corpus: dict[str, dict]
    scenarios: dict[str, Scenario]
    gateway: ScriptedGateway | None = None
    knowledge: ScriptedKnowledge | None = None
    web: ScriptedWeb | None = None

    def use(self, scenario: 'Scenario | str') -> Scenario:
        """Script the next run: a Scenario, or the id of a dataset case. Unknown ids raise LookupError."""
        if isinstance(scenario, str):
            if scenario not in self.scenarios:
                raise LookupError(f'sem cenário roteirizado para o caso {scenario}')
            scenario = self.scenarios[scenario]
        self.gateway = ScriptedGateway(scenario)
        self.knowledge = ScriptedKnowledge(self.corpus, scenario.passages, scenario.search_fails)
        self.web = ScriptedWeb(scenario.web)
        return scenario

    def runtime(self, settings, db) -> Runtime:
        async def close() -> None: ...
        return Runtime(self.gateway, self.knowledge, self.web, close)

    async def facts(self) -> dict:
        """The seeded receivable as stored: the truth the answers must match."""
        doc = await self.db.receivables.find_one({'workspace_id': 'demo', 'user_id': 'cliente1988'})
        return {'amount': Decimal(str(doc['amount'])), 'due_date': date.fromisoformat(doc['due_date']), 'sale_date': date.fromisoformat(doc['sale_date'])}


@asynccontextmanager
async def simulated_app(env: dict[str, str] | None = None):
    """Seeded app in this process on a throw-away database (dropped on exit), with a scripted runtime. Yields a Simulation.

    `env` overrides settings for the run (e.g. CHAT_WAIT_SECONDS); the process environment is restored on exit."""
    from evaluation.scenarios import SCENARIOS  # after sys.path is set; scenarios import this module's builders
    if not mongo_available():
        raise SimulationUnavailable(f'Mongo local indisponível em {MONGO_URI}: a rodada simulada precisa dele (docker compose up mongo).')
    name = f'eval_{uuid.uuid4().hex[:10]}'
    env = {'MONGO_URI': MONGO_URI, 'MONGO_DATABASE': name, 'APP_PROFILE': 'local-demo', 'DEMO_USER_ID': 'cliente1988', 'DEMO_WORKSPACE_ID': 'demo',
           'OPENAI_API_KEY': 'simulated-key', 'OTEL_EXPORTER_OTLP_ENDPOINT': '', 'CHAT_WAIT_SECONDS': '30', 'SSE_POLL_SECONDS': '0.02', **(env or {})}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    get_settings.cache_clear(), get_run_settings.cache_clear()
    client = AsyncMongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    app = FastAPI()
    app.state.mongo, app.state.tasks = client, {}
    try:
        db = client[name]
        await seed_demo(db, get_settings())
        await MongoConversations(db).ensure_indexes()
        register_error_handlers(app)
        app.include_router(routes.router)

        @app.get('/api/v1/health/ready')
        async def ready():  # lets the runner's readiness probe work against this app too
            return {'status': 'ok'}
        sim = Simulation(None, app, db, corpus_chunks(), SCENARIOS)
        app.state.runtime_factory = sim.runtime
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://simulated') as http:
            sim.http = http
            yield sim
    finally:
        tasks = list(app.state.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.drop_database(name)
        await client.close()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear(), get_run_settings.cache_clear()
