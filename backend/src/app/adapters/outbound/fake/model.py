"""Canned model for load tests (MODEL_PROVIDER=fake): the real turn structure with simulated latency, no network, no cost.

A run is router -> delegate to `knowledge` -> search_knowledge -> answer -> answer, so the API, Mongo event persistence,
leases and SSE are exercised as in a real run. Retrieval, embeddings and the provider are simulated; usage is reported
as unknown (None), so no token or cost number is ever invented.
"""
import asyncio
import json
from functools import lru_cache
from uuid import uuid4
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.domain.models import ModelEvent

ANSWER = ('Resposta simulada pelo provedor de testes de carga: nenhum modelo real foi chamado e o conteúdo não é '
          'orientação da Getnet. Prazos e condições variam conforme a modalidade [1]. ') * 2
CHUNK = {'chunk_id': 'fake-1', 'source_id': 'fake', 'title': 'Trecho simulado', 'heading': None, 'text': 'Conteúdo simulado para teste de carga.',
         'source_url': 'https://exemplo.invalid/simulado', 'retrieved_on': '2026-01-01', 'limitations': ['Conteúdo simulado.'],
         'document_type': 'curated_summary', 'index_version': 'fake', 'score': 1.0}


class FakeModelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=('../.env', '.env'), env_prefix='FAKE_MODEL_', extra='ignore')
    first_token_ms: float = Field(400, ge=0)  # every model turn waits this long before its first output
    delta_ms: float = Field(30, ge=0)  # pause between streamed text chunks
    deltas: int = Field(40, ge=1)  # text chunks per answer


@lru_cache
def get_fake_settings() -> FakeModelSettings:
    return FakeModelSettings()


def _call(name: str, **args) -> ModelEvent:
    return ModelEvent('function_call', data={'type': 'function_call', 'call_id': uuid4().hex, 'name': name, 'arguments': json.dumps(args)})


class FakeModel:
    """Plays model gateway, knowledge repository and embedder (like the doubles of the HTTP tests)."""

    def __init__(self, settings: FakeModelSettings | None = None):
        self.cfg = settings or get_fake_settings()

    async def stream(self, *, model, instructions, inputs, tools, max_output_tokens=None):
        await asyncio.sleep(self.cfg.first_token_ms / 1000)
        names = {tool['name']: tool for tool in tools}
        answered = any(item.get('type') == 'function_call_output' for item in inputs)
        task = str(inputs[0].get('content', ''))[:500]
        if 'delegate_to_agent' in names and not answered:
            destinations = names['delegate_to_agent']['parameters']['properties']['agent_id']['enum']
            yield _call('delegate_to_agent', agent_id='knowledge' if 'knowledge' in destinations else destinations[0], task=task)
        elif 'search_knowledge' in names and not answered:
            yield _call('search_knowledge', query=task)
        else:
            step = -(-len(ANSWER) // self.cfg.deltas)
            for start in range(0, len(ANSWER), step):
                await asyncio.sleep(self.cfg.delta_ms / 1000)
                yield ModelEvent('text_delta', text=ANSWER[start:start + step])
        yield ModelEvent('completed', data={'id': uuid4().hex, 'usage': None, 'output': []})

    async def embed(self, *, model, texts): return [[0.0] for _ in texts]
    async def hybrid_search(self, workspace_id, query, embedding, limit=5): return [dict(CHUNK)]
    async def close(self): pass
