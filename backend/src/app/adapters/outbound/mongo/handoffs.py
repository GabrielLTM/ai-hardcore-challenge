"""Human-handoff tickets (collection `handoffs`): opened by the escalation agent, then claimed, answered and closed by an operator.

States move only forward: aberto -> em_atendimento -> encerrado. While a ticket is `active` its conversation is with a human.
"""
import secrets
from datetime import datetime, timezone
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from app.adapters.outbound.mongo.conversations import decode_cursor, encode_cursor
from app.domain.handoff import CLOSED, IN_PROGRESS, OPEN
from app.domain.identity import Identity, Operator

_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no look-alikes (0/O, 1/I)


def new_protocol(now: datetime) -> str:
    return f"ATD-{now:%Y%m%d}-{''.join(secrets.choice(_ALPHABET) for _ in range(4))}"


def _who(operator: Operator) -> dict:
    return {'id': operator.id, 'name': operator.name}


class MongoHandoffRepository:
    def __init__(self, db, on_open=None):
        self.db, self.on_open = db, on_open  # on_open(): called once per ticket actually created (metrics)

    async def ensure_indexes(self):
        await self.db.handoffs.create_index('protocol', unique=True)
        # one ticket per run; tickets without a run (tests, tools called outside a run) are not constrained
        await self.db.handoffs.create_index([('workspace_id', 1), ('run_id', 1)], unique=True,
                                            partialFilterExpression={'run_id': {'$type': 'string'}})
        # at most one open or in-progress ticket per conversation
        await self.db.handoffs.create_index([('workspace_id', 1), ('conversation_id', 1)], unique=True, name='one_active_per_conversation',
                                            partialFilterExpression={'active': True})
        await self.db.handoffs.create_index([('workspace_id', 1), ('status', 1), ('created_at', 1)])

    async def open(self, who: Identity, *, run_id: str | None, conversation_id: str | None, reason: str, summary: str) -> dict:
        """Create the ticket for this run, or return the one already created by it or still active in the conversation (idempotent)."""
        active = await self.active_for(who, conversation_id)
        if active:
            return active
        for _ in range(5):  # a protocol collision is astronomically rare, but retry instead of failing the customer
            now = datetime.now(timezone.utc)
            protocol = new_protocol(now)
            doc = {'protocol': protocol, 'workspace_id': who.workspace_id, 'user_id': who.user_id, 'conversation_id': conversation_id,
                   'run_id': run_id, 'reason': reason, 'summary': summary, 'status': OPEN, 'created_at': now, 'updated_at': now,
                   'timeline': [{'kind': 'opened', 'at': now, 'run_id': run_id, 'reason': reason}],
                   **({'active': True} if conversation_id else {})}
            key = {'workspace_id': who.workspace_id, 'run_id': run_id} if run_id else {'protocol': protocol}
            try:
                ticket = await self.db.handoffs.find_one_and_update(key, {'$setOnInsert': doc}, upsert=True, projection={'_id': 0},
                                                                    return_document=ReturnDocument.AFTER)
                if ticket['protocol'] == protocol and self.on_open:
                    self.on_open()
                return ticket
            except DuplicateKeyError:
                existing = (await self.db.handoffs.find_one(key, {'_id': 0}) if run_id else None) or await self.active_for(who, conversation_id)
                if existing:
                    return existing
        raise RuntimeError('não foi possível gerar um protocolo único')

    async def active_for(self, who: Identity, conversation_id: str | None) -> dict | None:
        """The open or in-progress ticket of the customer's conversation, if any."""
        if not conversation_id:
            return None
        return await self.db.handoffs.find_one({'workspace_id': who.workspace_id, 'user_id': who.user_id,
                                                'conversation_id': conversation_id, 'active': True}, {'_id': 0})

    async def get(self, workspace_id: str, protocol: str) -> dict | None:
        return await self.db.handoffs.find_one({'workspace_id': workspace_id, 'protocol': protocol}, {'_id': 0})

    async def list_page(self, workspace_id: str, status: str, operator_id: str | None = None, limit: int = 30, cursor: str | None = None):
        """Open tickets oldest first (longest wait on top), the others newest first; keyset on (created_at, protocol)."""
        query: dict = {'workspace_id': workspace_id, 'status': status, **({'operator.id': operator_id} if operator_id else {})}
        oldest_first = status == OPEN
        if cursor:
            at, last = decode_cursor(cursor)
            op = '$gt' if oldest_first else '$lt'
            query = {'$and': [query, {'$or': [{'created_at': {op: at}}, {'created_at': at, 'protocol': {op: last}}]}]}
        order = 1 if oldest_first else -1
        rows = await self.db.handoffs.find(query, {'_id': 0, 'timeline': 0}).sort([('created_at', order), ('protocol', order)]) \
            .limit(limit + 1).to_list(length=limit + 1)
        page = rows[:limit]
        last_row = page[-1] if len(rows) > limit else None
        return page, (encode_cursor({'updated_at': last_row['created_at'], 'id': last_row['protocol']}) if last_row else None)

    async def claim(self, workspace_id: str, protocol: str, operator: Operator) -> dict | None:
        """Atomic: only one operator turns an open ticket into in-progress; None when it was not open anymore."""
        now = datetime.now(timezone.utc)
        return await self.db.handoffs.find_one_and_update(
            {'workspace_id': workspace_id, 'protocol': protocol, 'status': OPEN},
            {'$set': {'status': IN_PROGRESS, 'operator': _who(operator), 'claimed_at': now, 'updated_at': now},
             '$push': {'timeline': {'kind': 'claimed', 'at': now, 'operator': _who(operator)}}},
            projection={'_id': 0}, return_document=ReturnDocument.AFTER)

    async def operator_message(self, workspace_id: str, protocol: str, operator: Operator) -> dict | None:
        """Log a message sent to the customer; only the operator holding the in-progress ticket may (None otherwise)."""
        now = datetime.now(timezone.utc)
        return await self.db.handoffs.find_one_and_update(
            {'workspace_id': workspace_id, 'protocol': protocol, 'status': IN_PROGRESS, 'operator.id': operator.id},
            {'$set': {'updated_at': now}, '$push': {'timeline': {'kind': 'operator_message', 'at': now, 'operator': _who(operator)}}},
            projection={'_id': 0}, return_document=ReturnDocument.AFTER)

    async def note(self, workspace_id: str, protocol: str, operator: Operator, content: str) -> dict | None:
        """Internal note, any operator of the workspace, any state; it lives only in the timeline, never in the conversation."""
        now = datetime.now(timezone.utc)
        return await self.db.handoffs.find_one_and_update(
            {'workspace_id': workspace_id, 'protocol': protocol},
            {'$set': {'updated_at': now}, '$push': {'timeline': {'kind': 'note', 'at': now, 'operator': _who(operator), 'content': content}}},
            projection={'_id': 0}, return_document=ReturnDocument.AFTER)

    async def customer_message(self, who: Identity, protocol: str):
        now = datetime.now(timezone.utc)
        await self.db.handoffs.update_one({'workspace_id': who.workspace_id, 'protocol': protocol},
                                          {'$set': {'updated_at': now}, '$push': {'timeline': {'kind': 'customer_message', 'at': now}}})

    async def close(self, workspace_id: str, protocol: str, operator: Operator, resolution: str) -> dict | None:
        now = datetime.now(timezone.utc)
        return await self.db.handoffs.find_one_and_update(
            {'workspace_id': workspace_id, 'protocol': protocol, 'status': IN_PROGRESS, 'operator.id': operator.id},
            {'$set': {'status': CLOSED, 'resolution': resolution, 'closed_at': now, 'updated_at': now}, '$unset': {'active': ''},
             '$push': {'timeline': {'kind': 'closed', 'at': now, 'operator': _who(operator), 'resolution': resolution}}},
            projection={'_id': 0}, return_document=ReturnDocument.AFTER)

    async def cancel(self, who: Identity, protocol: str) -> dict | None:
        """The customer gives up before anyone claims the ticket; None once an attendant has it."""
        now = datetime.now(timezone.utc)
        return await self.db.handoffs.find_one_and_update(
            {'workspace_id': who.workspace_id, 'user_id': who.user_id, 'protocol': protocol, 'status': OPEN},
            {'$set': {'status': CLOSED, 'resolution': 'O cliente saiu da fila antes de ser atendido.', 'closed_at': now, 'updated_at': now},
             '$unset': {'active': ''}, '$push': {'timeline': {'kind': 'cancelled', 'at': now}}},
            projection={'_id': 0}, return_document=ReturnDocument.AFTER)
