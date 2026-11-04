from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.adapters.outbound.openai.gateway import OpenAIModelGateway


class Stream:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def __aiter__(self):
        yield SimpleNamespace(type='response.output_text.delta', delta='Olá')
        yield SimpleNamespace(type='response.completed', response=SimpleNamespace(id='r1', usage=None, output=[]))


@pytest.mark.asyncio
async def test_stream_normalizes_provider_events():
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=Stream())))
    gateway = OpenAIModelGateway(client)
    events = [event async for event in gateway.stream(model='test', instructions='rule', inputs=[], tools=[])]
    assert [(event.type, event.text) for event in events] == [('text_delta', 'Olá'), ('completed', '')]
    assert client.responses.create.await_args.kwargs['stream'] is True


@pytest.mark.asyncio
async def test_embedding_order_is_stable():
    data = [SimpleNamespace(index=1, embedding=[2.0]), SimpleNamespace(index=0, embedding=[1.0])]
    client = SimpleNamespace(embeddings=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(data=data))))
    assert await OpenAIModelGateway(client).embed(model='test', texts=['a', 'b']) == [[1.0], [2.0]]
