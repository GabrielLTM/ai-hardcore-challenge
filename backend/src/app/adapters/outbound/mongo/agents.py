"""Agent configuration persistence and the published snapshot used by runs.

Collections: agents (mutable drafts with `revision`), agent_versions / prompt_versions (immutable),
graph_versions (snapshot pinning one agent version per node), workspaces (current graph pointer, the CAS token)
and audit_events.
"""
import re
from datetime import datetime
from typing import Any
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from app.domain.agents import GraphConflict, Publication, RevisionConflict
from app.domain.models import Agent, AgentKind

NO_ID = {'_id': 0}


def _agent(row: dict[str, Any]) -> Agent:
    return Agent(row['agent_id'], row['name'], row['description'], row['instructions'], AgentKind(row['kind']),
                 row['model'], tuple(row['tools']), tuple(row['delegates']), row['version'], row.get('limits') or {})


async def load_published_snapshot(db, workspace_id: str) -> tuple[str | None, dict[str, Agent]]:
    """Return (graph_version_id, agents) for the workspace's current published graph.

    Agents come from the versions pinned by that snapshot, so a run is unaffected by later publications."""
    version, versions = await MongoAgentRepository(db).graph_state(workspace_id)
    agents = {agent_id: _agent(row) for agent_id, row in versions.items() if row['enabled']}
    return (str(version) if version else None), agents


class MongoAgentRepository:
    def __init__(self, db): self.db = db

    async def ensure_indexes(self):
        await self.db.workspaces.create_index('id', unique=True)
        await self.db.agents.create_index([('workspace_id', 1), ('id', 1)], unique=True)
        await self.db.agents.create_index([('workspace_id', 1), ('created_at', 1), ('id', 1)])
        await self.db.agent_versions.create_index([('workspace_id', 1), ('agent_id', 1), ('version', 1)], unique=True)
        await self.db.prompt_versions.create_index([('workspace_id', 1), ('prompt_id', 1), ('version', 1)], unique=True)
        await self.db.graph_versions.create_index([('workspace_id', 1), ('version', 1)], unique=True)
        await self.db.audit_events.create_index([('workspace_id', 1), ('at', -1)])

    # drafts
    async def get_agent(self, ws: str, agent_id: str):
        return await self.db.agents.find_one({'workspace_id': ws, 'id': agent_id}, NO_ID)

    async def get_agent_by_prompt(self, ws: str, prompt_id: str):
        return await self.db.agents.find_one({'workspace_id': ws, 'prompt_id': prompt_id}, NO_ID)

    async def get_agents(self, ws: str, ids: list[str]):
        return await self.db.agents.find({'workspace_id': ws, 'id': {'$in': ids}}, NO_ID).to_list()

    async def drafts_delegating_to(self, ws: str, agent_id: str):
        return await self.db.agents.find({'workspace_id': ws, 'delegates': agent_id}, NO_ID).to_list(length=200)

    async def insert_agent(self, ws: str, draft: dict[str, Any]) -> bool:
        try:
            await self.db.agents.insert_one({'workspace_id': ws, **draft})
        except DuplicateKeyError:
            return False
        return True

    async def seed_agent(self, ws: str, draft: dict[str, Any]):
        """Insert only when absent. Documents without `revision` come from the pre-versioning seed and were never admin-edited."""
        await self.db.agents.replace_one({'workspace_id': ws, 'id': draft['id'], 'revision': {'$exists': False}}, {'workspace_id': ws, **draft})
        await self.insert_agent(ws, draft)

    async def list_agents(self, ws: str, *, q: str | None, kind: str | None, status: str | None, limit: int,
                          after: tuple[datetime, str] | None):
        query: dict[str, Any] = {'workspace_id': ws}
        if kind:
            query['kind'] = kind
        if status:
            query['published'] = None if status == 'draft' else {'$ne': None}
        if q:
            pattern = {'$regex': re.escape(q), '$options': 'i'}
            query['$or'] = [{'name': pattern}, {'description': pattern}, {'id': pattern}]
        if after:
            query = {'$and': [query, {'$or': [{'created_at': {'$gt': after[0]}}, {'created_at': after[0], 'id': {'$gt': after[1]}}]}]}
        return await self.db.agents.find(query, NO_ID).sort([('created_at', 1), ('id', 1)]).limit(limit).to_list(length=limit)

    async def update_draft(self, ws: str, agent_id: str, expected_revision: int, fields: dict[str, Any]):
        """Compare-and-set on `revision`; None when the revision is stale."""
        return await self.db.agents.find_one_and_update(
            {'workspace_id': ws, 'id': agent_id, 'revision': expected_revision},
            {'$set': fields, '$inc': {'revision': 1}}, projection=NO_ID, return_document=ReturnDocument.AFTER)

    # versions
    async def list_versions(self, ws: str, collection: str, key: dict[str, Any], before: int | None, limit: int):
        query = {'workspace_id': ws, **key, **({'version': {'$lt': before}} if before else {})}
        return await self.db[collection].find(query, NO_ID).sort('version', -1).limit(limit).to_list(length=limit)

    async def get_version(self, ws: str, agent_id: str, version: int):
        return await self.db.agent_versions.find_one({'workspace_id': ws, 'agent_id': agent_id, 'version': version}, NO_ID)

    # published graph
    async def graph_state(self, ws: str, session=None) -> tuple[int, dict[str, dict[str, Any]]]:
        """Current graph version number and the agent version document pinned for each node."""
        row = await self.db.workspaces.find_one({'id': ws}, NO_ID, session=session)
        version = row.get('graph_version', 0) if row else 0
        if not version:
            return 0, {}
        snapshot = await self.db.graph_versions.find_one({'workspace_id': ws, 'version': version}, NO_ID, session=session)
        pins = [{'agent_id': agent_id, 'version': v} for agent_id, v in snapshot['nodes'].items()]
        rows = self.db.agent_versions.find({'workspace_id': ws, '$or': pins}, NO_ID, session=session)
        return version, {row['agent_id']: row async for row in rows}

    async def commit_publication(self, ws: str, expected_graph_version: int, publications: list[Publication], *,
                                 actor: str, at: datetime, summary: str) -> int:
        """Publish atomically: the workspace graph version is a CAS token, so two publications validated
        against the same snapshot cannot both commit. Raises GraphConflict or RevisionConflict."""
        try:
            await self.db.workspaces.update_one({'id': ws}, {'$setOnInsert': {'graph_version': 0}}, upsert=True)
        except DuplicateKeyError:
            pass
        new_version = expected_graph_version + 1

        async def work(session):
            moved = await self.db.workspaces.find_one_and_update(
                {'id': ws, 'graph_version': expected_graph_version},
                {'$set': {'graph_version': new_version, 'updated_at': at}}, session=session)
            if moved is None:
                raise GraphConflict()
            nodes: dict[str, int] = {}
            if expected_graph_version:
                previous = await self.db.graph_versions.find_one({'workspace_id': ws, 'version': expected_graph_version}, session=session)
                nodes = dict(previous['nodes'])
            for p in publications:
                nodes[p.agent_id] = p.version['version']
                if p.prompt:
                    await self.db.prompt_versions.insert_one({'workspace_id': ws, **p.prompt}, session=session)
                await self.db.agent_versions.insert_one({'workspace_id': ws, **p.version}, session=session)
                pointer = {k: v for k, v in p.version.items() if k not in ('agent_id', 'prompt_id')}
                update: dict[str, Any] = {'$set': {'published': pointer, **(p.draft_update or {})}}
                if p.draft_update:
                    update['$set'].update(updated_at=at, updated_by=actor)
                    update['$inc'] = {'revision': 1}
                done = await self.db.agents.update_one({'workspace_id': ws, 'id': p.agent_id, 'revision': p.expected_revision}, update, session=session)
                if not done.matched_count:
                    raise RevisionConflict()
                await self.db.audit_events.insert_one({'workspace_id': ws, 'type': 'agent.restored' if p.version.get('restored_from') else 'agent.published', 'at': at, 'actor': actor,
                    'agent_id': p.agent_id, 'version': p.version['version'], 'graph_version': new_version,
                    'summary': p.version['summary'], 'restored_from': p.version.get('restored_from')}, session=session)
            await self.db.graph_versions.insert_one({'workspace_id': ws, 'version': new_version, 'nodes': nodes, 'summary': summary,
                'trigger_agent_ids': [p.agent_id for p in publications], 'created_by': actor, 'created_at': at}, session=session)

        async with self.db.client.start_session() as session:
            await session.with_transaction(work)
        return new_version
