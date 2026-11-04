from typing import Protocol
from app.domain.identity import Identity
from app.domain.support import Receivable, Terminal


class SupportRepository(Protocol):
    async def get_receivables(self, identity: Identity) -> list[Receivable]: ...
    async def get_terminals(self, identity: Identity) -> list[Terminal]: ...
