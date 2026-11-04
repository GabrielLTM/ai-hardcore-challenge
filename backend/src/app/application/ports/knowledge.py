"""Ports for official-page collection and the ingestion queue."""
from dataclasses import dataclass
from typing import Protocol


class SourceError(Exception):
    """A source failed in one phase; `code` is stable for the UI, `message` is pt-BR and free of secrets."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class LeaseLost(Exception):
    """The worker no longer owns the job (lease expired and another worker claimed it)."""


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str
    content_type: str
    body: bytes


class Fetcher(Protocol):
    async def fetch(self, url: str) -> FetchedPage: ...
