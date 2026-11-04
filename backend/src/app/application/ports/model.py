"""Application port for model generation; domain never imports a provider SDK."""
from collections.abc import AsyncIterator
from typing import Any, Protocol
from app.domain.models import ModelEvent


class ModelGateway(Protocol):
    def stream(self, *, model: str, instructions: str, inputs: list[dict[str, Any]], tools: list[dict[str, Any]],
               max_output_tokens: int | None = None) -> AsyncIterator[ModelEvent]:
        """`max_output_tokens` is only passed when the agent configures one."""
        ...

    async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]: ...
