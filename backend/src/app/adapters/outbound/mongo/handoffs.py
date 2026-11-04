"""Human-handoff tickets (collection `handoffs`). Demonstration only: nothing is sent to a real operator queue."""
import secrets
from datetime import datetime, timezone
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from app.domain.identity import Identity

_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no look-alikes (0/O, 1/I)


def new_protocol(now: datetime) -> str:
    return f"ATD-{now:%Y%m%d}-{''.join(secrets.choice(_ALPHABET) for _ in range(4))}"


class MongoHandoffRepository:
    def __init__(self, db): self.db = db

    async def ensure_indexes(self):
        await self.db.handoffs.create_index('protocol', unique=True)
        # one ticket per run; tickets without a run (tests, tools called outside a run) are not constrained
        await self.db.handoffs.create_index([('workspace_id', 1), ('run_id', 1)], unique=True,
                                            partialFilterExpression={'run_id': {'$type': 'string'}})

    async def open(self, who: Identity, *, run_id: str | None, conversation_id: str | None, reason: str, summary: str) -> dict:
        """Create the ticket for this run, or return the one already created by it (idempotent)."""
        for _ in range(5):  # a protocol collision is astronomically rare, but retry instead of failing the customer
            now = datetime.now(timezone.utc)
            protocol = new_protocol(now)
            doc = {'protocol': protocol, 'workspace_id': who.workspace_id, 'user_id': who.user_id, 'conversation_id': conversation_id,
                   'run_id': run_id, 'reason': reason, 'summary': summary, 'status': 'aberto', 'created_at': now, 'updated_at': now}
            key = {'workspace_id': who.workspace_id, 'run_id': run_id} if run_id else {'protocol': protocol}
            try:
                return await self.db.handoffs.find_one_and_update(key, {'$setOnInsert': doc}, upsert=True, projection={'_id': 0},
                                                                  return_document=ReturnDocument.AFTER)
            except DuplicateKeyError:
                existing = await self.db.handoffs.find_one(key, {'_id': 0}) if run_id else None
                if existing:
                    return existing
        raise RuntimeError('não foi possível gerar um protocolo único')
