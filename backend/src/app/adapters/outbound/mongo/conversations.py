"""Persistence for conversations, runs and their ordered event streams."""
import base64
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure
from app.domain.errors import CapacityExceeded, ConversationBusy, ConversationNotFound, IdempotencyConflict
from app.domain.identity import Identity

ACTIVE = ['queued', 'running']
TERMINAL = ('completed', 'failed', 'cancelled', 'interrupted')


def now(): return datetime.now(timezone.utc)


def scope(identity: Identity):
    return {'workspace_id': identity.workspace_id, 'user_id': identity.user_id}


def encode_cursor(row: dict) -> str:
    at = row['updated_at'].replace(tzinfo=row['updated_at'].tzinfo or timezone.utc).isoformat()
    return base64.urlsafe_b64encode(json.dumps([at, row['id']]).encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        at, last_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(at), str(last_id)
    except (ValueError, TypeError) as exc:
        raise ValueError('invalid_cursor') from exc


class MongoConversations:
    def __init__(self, db): self.db = db

    async def _transaction(self, work):
        """Run `work(session)` in a transaction, retrying transient conflicts (heartbeats write the same run document)."""
        async with self.db.client.start_session() as session:
            return await session.with_transaction(work)

    async def ensure_indexes(self, event_retention_days: int = 7):
        await self.db.conversations.create_index([('workspace_id', 1), ('user_id', 1), ('updated_at', -1)])
        await self.db.messages.create_index([('conversation_id', 1), ('created_at', 1)])
        await self.db.runs.create_index([('workspace_id', 1), ('user_id', 1), ('conversation_id', 1), ('idempotency_key', 1)], unique=True)
        await self.db.runs.create_index([('workspace_id', 1), ('status', 1)])
        await self.db.run_quota.create_index('workspace_id', unique=True)
        await self.db.runs.create_index([('status', 1), ('lease_expires_at', 1)])
        await self.db.run_events.create_index([('run_id', 1), ('seq', 1)], unique=True)
        await self.db.run_evidence.create_index([('workspace_id', 1), ('user_id', 1), ('run_id', 1)], unique=True)
        ttl = event_retention_days * 86400
        try:
            await self.db.run_events.create_index('occurred_at', expireAfterSeconds=ttl, name='run_events_ttl')
        except OperationFailure:  # retention changed: update the existing TTL index in place
            await self.db.command('collMod', 'run_events', index={'name': 'run_events_ttl', 'expireAfterSeconds': ttl})

    async def create_conversation(self, identity: Identity, title: str = 'Nova conversa'):
        doc = {'id': uuid4().hex, **scope(identity), 'title': title, 'created_at': now(), 'updated_at': now()}
        await self.db.conversations.insert_one(doc)
        doc.pop('_id', None)
        return doc

    async def list_conversations(self, identity: Identity, q: str | None = None, limit: int = 30, cursor: str | None = None):
        """Newest first, keyset pagination on (updated_at, id); `cursor` is opaque to clients. Returns (items, next_cursor)."""
        query: dict = scope(identity)
        if q:
            query['title'] = {'$regex': re.escape(q), '$options': 'i'}
        if cursor:
            at, last_id = decode_cursor(cursor)
            query = {'$and': [query, {'$or': [{'updated_at': {'$lt': at}}, {'updated_at': at, 'id': {'$lt': last_id}}]}]}
        rows = await self.db.conversations.find(query, {'_id': 0}).sort([('updated_at', -1), ('id', -1)]).limit(limit + 1).to_list(length=limit + 1)
        page = rows[:limit]
        return page, (encode_cursor(page[-1]) if len(rows) > limit else None)

    async def get_conversation(self, identity: Identity, conversation_id: str):
        return await self.db.conversations.find_one({**scope(identity), 'id': conversation_id}, {'_id': 0})

    async def messages(self, identity: Identity, conversation_id: str):
        return await self.db.messages.find({**scope(identity), 'conversation_id': conversation_id}, {'_id': 0}).sort('created_at', 1).to_list(length=500)

    async def create_run(self, identity: Identity, conversation_id: str, message: str, key: str, *,
                         lease_seconds: float = 30, max_active: int | None = None, trace_id: str | None = None):
        """Return (run, created). Same key + same message replays the original run; another message raises IdempotencyConflict."""
        request_hash = hashlib.sha256(message.encode()).hexdigest()
        query = {**scope(identity), 'conversation_id': conversation_id, 'idempotency_key': key}

        def replay(existing):
            if existing['request_hash'] != request_hash: raise IdempotencyConflict()
            return existing, False

        existing = await self.db.runs.find_one(query, {'_id': 0})
        if existing: return replay(existing)
        if max_active is not None:
            try:
                await self.db.run_quota.update_one({'workspace_id': identity.workspace_id}, {'$setOnInsert': {'v': 0}}, upsert=True)
            except DuplicateKeyError:
                pass  # created concurrently
        run = {**query, 'id': uuid4().hex, 'request_hash': request_hash, 'status': 'queued', 'seq': 0,
               'partial_text': '', 'trace_id': trace_id, 'lease_expires_at': now() + timedelta(seconds=lease_seconds),
               'created_at': now(), 'updated_at': now()}
        message_doc = {**scope(identity), 'id': uuid4().hex, 'conversation_id': conversation_id,
                       'run_id': run['id'], 'role': 'user', 'content': message, 'created_at': now()}

        async def work(session):
            if max_active is not None:
                # Serialises run creation per workspace: concurrent transactions conflict on this write and retry,
                # so the count below always sees runs committed by the others.
                await self.db.run_quota.update_one({'workspace_id': identity.workspace_id}, {'$inc': {'v': 1}}, session=session)
                active = await self.db.runs.count_documents({'workspace_id': identity.workspace_id, 'status': {'$in': ACTIVE}}, session=session)
                if active >= max_active: raise CapacityExceeded()
            conversation = await self.db.conversations.find_one_and_update(
                {**scope(identity), 'id': conversation_id, 'active_run_id': {'$exists': False}},
                {'$set': {'active_run_id': run['id'], 'last_run_status': 'running', 'updated_at': now()}}, session=session)
            if not conversation: return False
            await self.db.runs.insert_one(dict(run), session=session)
            await self.db.messages.insert_one(dict(message_doc), session=session)
            return True
        try:
            if await self._transaction(work): return run, True
        except DuplicateKeyError:
            pass  # a concurrent request with the same key won the insert
        existing = await self.db.runs.find_one(query, {'_id': 0})
        if existing: return replay(existing)
        raise ConversationBusy() if await self.get_conversation(identity, conversation_id) else ConversationNotFound()

    async def get_run(self, identity: Identity, run_id: str):
        return await self.db.runs.find_one({**scope(identity), 'id': run_id}, {'_id': 0})

    async def claim_run(self, identity: Identity, run_id: str, owner: str, lease_seconds: float) -> bool:
        """queued -> running by the executing instance, which now owns the lease."""
        result = await self.db.runs.update_one({**scope(identity), 'id': run_id, 'status': 'queued'},
            {'$set': {'status': 'running', 'lease_owner': owner, 'lease_expires_at': now() + timedelta(seconds=lease_seconds),
                      'heartbeat_at': now(), 'updated_at': now()}})
        return result.modified_count == 1

    async def heartbeat(self, identity: Identity, run_id: str, lease_seconds: float):
        """Renew the lease; returns the run (for cancel_requested) or None when it is no longer active."""
        return await self.db.runs.find_one_and_update(
            {**scope(identity), 'id': run_id, 'status': {'$in': ACTIVE}},
            {'$set': {'lease_expires_at': now() + timedelta(seconds=lease_seconds), 'heartbeat_at': now()}},
            projection={'_id': 0, 'cancel_requested': 1}, return_document=ReturnDocument.AFTER)

    async def request_cancel(self, identity: Identity, run_id: str):
        await self.db.runs.update_one({**scope(identity), 'id': run_id, 'status': {'$in': ACTIVE}},
                                      {'$set': {'cancel_requested': True, 'updated_at': now()}})

    async def set_fields(self, identity: Identity, run_id: str, fields: dict):
        await self.db.runs.update_one({**scope(identity), 'id': run_id}, {'$set': fields})

    async def append_event(self, identity: Identity, run_id: str, kind: str, payload: dict):
        async def work(session):
            fields = {'seq': {'$add': ['$seq', 1]}, 'updated_at': now()}
            if kind == 'response.delta':
                fields['partial_text'] = {'$concat': ['$partial_text', {'$literal': payload.get('text', '')}]}
            run = await self.db.runs.find_one_and_update({**scope(identity), 'id': run_id}, [{'$set': fields}],
                return_document=ReturnDocument.AFTER, session=session)
            if not run: raise ValueError('run_missing')
            event = {**scope(identity), 'run_id': run_id, 'seq': run['seq'], 'type': kind, 'occurred_at': now(), 'payload': payload}
            await self.db.run_events.insert_one(dict(event), session=session)
            return event
        return await self._transaction(work)

    async def events(self, identity: Identity, run_id: str, after: int, limit: int = 200):
        return await self.db.run_events.find({**scope(identity), 'run_id': run_id, 'seq': {'$gt': after}}, {'_id': 0}).sort('seq', 1).to_list(length=limit)

    async def first_seq(self, identity: Identity, run_id: str) -> int | None:
        """Lowest event seq still stored (older ones may have been removed by retention)."""
        first = await self.db.run_events.find_one({**scope(identity), 'run_id': run_id}, {'seq': 1}, sort=[('seq', 1)])
        return first['seq'] if first else None

    async def finish(self, identity: Identity, run_id: str, status: str, text: str | None, *, payload: dict | None = None,
                     sources: list | None = None, error: dict | None = None, only_if: dict | None = None) -> bool:
        """Persist the terminal state, its event and the assistant message in one transaction.

        The first terminal state wins: a run that already left queued/running is left untouched and False is returned.
        `text=None` keeps the partial text already persisted.
        """
        async def work(session):
            fields = {'status': status, 'updated_at': now(), 'seq': {'$add': ['$seq', 1]}, 'sources': {'$literal': sources or []}}
            if text is not None: fields['partial_text'] = {'$literal': text}
            if error: fields['error'] = {'$literal': error}
            run = await self.db.runs.find_one_and_update(
                {**scope(identity), 'id': run_id, 'status': {'$in': ACTIVE}, **(only_if or {})}, [{'$set': fields}],
                return_document=ReturnDocument.AFTER, session=session)
            if not run: return False
            await self.db.run_events.insert_one({**scope(identity), 'run_id': run_id, 'seq': run['seq'], 'type': f'run.{status}',
                                                 'occurred_at': now(), 'payload': payload or {}}, session=session)
            await self.db.messages.insert_one({**scope(identity), 'id': uuid4().hex, 'conversation_id': run['conversation_id'],
                'run_id': run_id, 'role': 'assistant', 'content': run['partial_text'], 'status': status, 'sources': sources or [],
                **({'error': error} if error else {}), 'created_at': now()}, session=session)
            await self.db.conversations.update_one({**scope(identity), 'id': run['conversation_id'], 'active_run_id': run_id},
                {'$unset': {'active_run_id': ''}, '$set': {'last_run_status': status, 'updated_at': now()}}, session=session)
            return True
        return await self._transaction(work)

    async def interrupt_expired(self, limit: int = 50) -> int:
        """Mark runs whose executing instance stopped renewing its lease as interrupted, keeping the partial text."""
        expired = {'lease_expires_at': {'$lt': now()}}
        runs = await self.db.runs.find({'status': {'$in': ACTIVE}, **expired}, {'_id': 0}).limit(limit).to_list(length=limit)
        count = 0
        for run in runs:
            who = Identity(run['user_id'], run['workspace_id'], 'system')
            count += await self.finish(who, run['id'], 'interrupted', None, only_if=expired, payload={
                'code': 'lease_expired', 'message': 'A execução foi interrompida antes de concluir; envie novamente.'},
                error={'code': 'interrupted', 'message': 'A execução foi interrompida antes de concluir.'})
        return count

    async def save_evidence(self, identity: Identity, run_id: str, retrieved: list, cited_ids: list[int], invalid: list[int]):
        cited = set(cited_ids)
        await self.db.run_evidence.update_one({**scope(identity), 'run_id': run_id},
            {'$set': {'retrieved': [{**i, 'cited': i['evidence_id'] in cited} for i in retrieved],
                      'invalid_citations': invalid, 'updated_at': now()}}, upsert=True)

    async def get_evidence(self, identity: Identity, run_id: str):
        return await self.db.run_evidence.find_one({**scope(identity), 'run_id': run_id}, {'_id': 0})
