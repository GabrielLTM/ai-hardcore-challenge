"""OpenAI Responses and embeddings adapter."""
from collections.abc import AsyncIterator
from typing import Any
from openai import AsyncOpenAI
from app.domain.models import ModelEvent


class OpenAIModelGateway:
    def __init__(self, client: AsyncOpenAI):
        self.client = client

    async def stream(self, *, model: str, instructions: str, inputs: list[dict[str, Any]],
                     tools: list[dict[str, Any]], max_output_tokens: int | None = None) -> AsyncIterator[ModelEvent]:
        cap = {'max_output_tokens': max_output_tokens} if max_output_tokens else {}
        stream = await self.client.responses.create(
            model=model, instructions=instructions, input=inputs, tools=tools, stream=True, **cap
        )
        async with stream:
            async for event in stream:
                if event.type == 'response.output_text.delta':
                    yield ModelEvent(type='text_delta', text=event.delta)
                elif event.type == 'response.output_item.done':
                    item = event.item
                    if item.type == 'function_call':
                        yield ModelEvent(type='function_call', data=item.model_dump(by_alias=True, exclude_none=True))
                elif event.type == 'response.completed':
                    response = event.response
                    yield ModelEvent(type='completed', data={
                        'id': response.id,
                        'usage': response.usage.model_dump() if response.usage else None,
                        # by_alias: echoed back as input, so wire names ('async', not 'async_') are required
                        'output': [item.model_dump(by_alias=True, exclude_none=True) for item in response.output],
                    })
                elif event.type in ('response.failed', 'error'):
                    yield ModelEvent(type='error', data=event.model_dump())

    async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]:
        result = await self.client.embeddings.create(model=model, input=texts)
        return [item.embedding for item in sorted(result.data, key=lambda item: item.index)]
