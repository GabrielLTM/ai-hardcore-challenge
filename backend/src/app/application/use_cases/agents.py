"""Agent administration: drafts (ETag/revision), relationship checks, publication, versions and restore.

`repo` is a MongoAgentRepository-compatible store; HTTP concerns (statuses, headers) stay in the router."""
import base64
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from app.domain.agents import (AgentError, GraphConflict, RevisionConflict, changed_fields, contains_secret, content,
                               diff_versions, new_draft, plan_publication, slugify)
from app.domain.graph import ROOT_AGENT, GraphIssue, Node, check_graph
from app.domain.identity import Identity

ATTEMPTS = 3  # a publication that lost the graph CAS is revalidated against the new snapshot this many times


def now() -> datetime: return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat().replace('+00:00', 'Z')


def encode_cursor(value: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


def decode_cursor(cursor: str | None) -> Any:
    if not cursor:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except ValueError:
        raise AgentError('invalid_cursor', 'Cursor inválido.') from None


def node(doc: dict[str, Any]) -> Node:
    return Node(tuple(doc['delegates']), tuple(doc['tools']), doc['enabled'])


def issue_view(i: GraphIssue) -> dict[str, Any]:
    return {'code': i.code, 'message': i.message, 'agent_id': i.agent_id, 'path': list(i.path)}


def _stamped(doc: dict[str, Any]) -> dict[str, Any]:
    return {**doc, 'created_at': iso(doc['created_at'])}


def present(doc: dict[str, Any], full: bool = True) -> dict[str, Any]:
    published = doc['published']
    view = {'id': doc['id'], 'name': doc['name'], 'description': doc['description'], 'kind': doc['kind'],
            'enabled': doc['enabled'], 'tools': doc['tools'], 'delegates': doc['delegates'], 'revision': doc['revision'],
            'status': 'published' if published else 'draft', 'version': published['version'] if published else None,
            'has_unpublished_changes': published is None or bool(changed_fields(published, doc)),
            'updated_at': iso(doc['updated_at'])}
    if full:
        view |= {'instructions': doc['instructions'], 'model': doc['model'], 'limits': doc['limits'], 'prompt_id': doc['prompt_id'],
                 'created_at': iso(doc['created_at']), 'updated_by': doc['updated_by'],
                 'published': _stamped(published) if published else None}
    return view


def version_view(row: dict[str, Any], current: int | None, full: bool) -> dict[str, Any]:
    view = {k: row.get(k) for k in ('version', 'prompt_id', 'prompt_version', 'summary', 'created_by', 'restored_from')}
    view |= {'created_at': iso(row['created_at']), 'is_current': row['version'] == current, 'name': row['name']}
    return view | (content(row) if full else {})


def _page(rows: list[dict[str, Any]], limit: int, view, cursor_of) -> dict[str, Any]:
    items = rows[:limit]
    return {'items': [view(r) for r in items], 'next_cursor': cursor_of(items[-1]) if len(rows) > limit else None}


class AgentService:
    def __init__(self, repo, default_model: str):
        self.repo, self.default_model = repo, default_model

    async def _draft(self, who: Identity, agent_id: str) -> dict[str, Any]:
        draft = await self.repo.get_agent(who.workspace_id, agent_id)
        if not draft:
            raise AgentError('agent_not_found', 'Agente não encontrado.')
        return draft

    @staticmethod
    def _stale(draft: dict[str, Any]) -> AgentError:
        return AgentError('revision_conflict', 'O agente foi alterado por outra sessão. Recarregue ou compare antes de salvar.',
                          {'current_revision': draft['revision']})

    @staticmethod
    def _reject_secrets(data: dict[str, Any]) -> None:
        if contains_secret(*(str(data.get(k, '')) for k in ('name', 'description', 'instructions'))):
            raise AgentError('secret_detected', 'O texto parece conter uma credencial. Configurações de agentes não armazenam segredos.')

    # drafts
    async def list_agents(self, who: Identity, *, q: str | None, kind: str | None, status: str | None, limit: int, cursor: str | None):
        after = decode_cursor(cursor)
        try:
            after = (datetime.fromisoformat(after[0]), after[1]) if after else None
        except (ValueError, TypeError, IndexError):
            raise AgentError('invalid_cursor', 'Cursor inválido.') from None
        rows = await self.repo.list_agents(who.workspace_id, q=q, kind=kind, status=status, limit=limit + 1, after=after)
        return _page(rows, limit, lambda r: present(r, full=False), lambda r: encode_cursor([iso(r['created_at']), r['id']]))

    async def create(self, who: Identity, data: dict[str, Any]) -> dict[str, Any]:
        self._reject_secrets(data)
        fields = {'description': '', 'kind': 'custom', 'tools': [], 'delegates': [], 'limits': {}, 'enabled': True,
                  **data, 'model': data.get('model') or self.default_model}
        draft = new_draft(f"{slugify(data['name'])}-{uuid4().hex[:8]}", fields, who.user_id, now())
        if not await self.repo.insert_agent(who.workspace_id, draft):
            raise AgentError('id_conflict', 'Não foi possível gerar um identificador único. Tente novamente.')
        return present(draft)

    async def get(self, who: Identity, agent_id: str) -> dict[str, Any]:
        return present(await self._draft(who, agent_id))

    async def update(self, who: Identity, agent_id: str, expected_revision: int, patch: dict[str, Any]) -> dict[str, Any]:
        draft = await self._draft(who, agent_id)
        if draft['revision'] != expected_revision:
            raise self._stale(draft)
        self._reject_secrets(patch)
        changes = {k: v for k, v in patch.items() if draft[k] != v}
        if not changes:
            return present(draft)
        updated = await self.repo.update_draft(who.workspace_id, agent_id, expected_revision,
                                               {**changes, 'updated_at': now(), 'updated_by': who.user_id})
        if updated is None:
            raise self._stale(await self._draft(who, agent_id))
        return present(updated)

    # relationships
    async def relationships(self, who: Identity, agent_id: str, state: str) -> dict[str, Any]:
        draft = await self._draft(who, agent_id)
        ws = who.workspace_id
        if state == 'published':
            graph_version, versions = await self.repo.graph_state(ws)
            entry = lambda v: {'id': v['agent_id'], 'name': v['name'], 'version': v['version'], 'status': 'published', 'enabled': v['enabled']}
            mine = versions.get(agent_id)
            return {'agent_id': agent_id, 'state': state, 'graph_version': str(graph_version) if graph_version else None,
                    'outbound': [entry(versions[t]) for t in (mine['delegates'] if mine else []) if t in versions],
                    'inbound': [entry(v) for v in versions.values() if agent_id in v['delegates']]}
        targets = {a['id']: a for a in await self.repo.get_agents(ws, draft['delegates'])}

        def entry(a):  # draft state: show each agent's draft name with its published version, if any
            return {'id': a['id'], 'name': a['name'], 'version': a['published']['version'] if a['published'] else None,
                    'status': 'published' if a['published'] else 'draft', 'enabled': a['enabled']}
        return {'agent_id': agent_id, 'state': state, 'graph_version': None,
                'outbound': [entry(targets[t]) if t in targets else {'id': t, 'name': None, 'version': None, 'status': 'missing', 'enabled': False}
                             for t in draft['delegates']],
                'inbound': [entry(a) for a in await self.repo.drafts_delegating_to(ws, agent_id)]}

    async def validate(self, who: Identity, agent_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        """Check the proposed links against the current published graph. Never saves."""
        draft = await self._draft(who, agent_id)
        graph_version, versions = await self.repo.graph_state(who.workspace_id)
        candidate = {**draft, **{k: v for k, v in proposal.items() if k in ('delegates', 'tools', 'enabled')}}
        issues = check_graph({**{k: node(v) for k, v in versions.items()}, agent_id: node(candidate)}, root=ROOT_AGENT)
        wanted = proposal.get('graph_revision')
        return {'valid': not issues, 'graph_revision': graph_version, 'stale': wanted is not None and wanted != graph_version,
                'errors': [issue_view(i) for i in issues], 'cycle': next((list(i.path) for i in issues if i.code == 'cycle'), None)}

    # publication
    async def _publish(self, who: Identity, agent_id: str, build, if_match: int | None) -> dict[str, Any]:
        ws = who.workspace_id
        for _ in range(ATTEMPTS):
            draft = await self._draft(who, agent_id)
            if if_match is not None and draft['revision'] != if_match:
                raise self._stale(draft)
            pub = build(draft, now())
            graph_version, versions = await self.repo.graph_state(ws)
            issues = check_graph({**{k: node(v) for k, v in versions.items()}, agent_id: node(pub.version)}, root=ROOT_AGENT)
            if issues:
                raise AgentError('graph_invalid', f'{issues[0].message} A versão publicada anterior continua ativa.',
                                 {'errors': [issue_view(i) for i in issues],
                                  'cycle': next((list(i.path) for i in issues if i.code == 'cycle'), None)})
            try:
                new_graph = await self.repo.commit_publication(ws, graph_version, [pub], actor=who.user_id,
                                                               at=pub.version['created_at'], summary=pub.version['summary'])
            except GraphConflict:
                continue
            except RevisionConflict:
                raise self._stale(await self._draft(who, agent_id)) from None
            return {'agent': await self.get(who, agent_id), 'version': pub.version['version'],
                    'prompt_version': pub.version['prompt_version'], 'graph_version': str(new_graph)}
        raise AgentError('graph_conflict', 'O grafo foi alterado por outra publicação. Revise e publique novamente.')

    async def publish(self, who: Identity, agent_id: str, *, if_match: int | None, summary: str | None) -> dict[str, Any]:
        def build(draft, at):
            if draft['published'] and not changed_fields(draft['published'], draft):
                raise AgentError('no_changes', 'Não há alterações para publicar.')
            return plan_publication(draft, draft, actor=who.user_id, at=at, summary=summary)
        return await self._publish(who, agent_id, build, if_match)

    async def restore(self, who: Identity, agent_id: str, version: int, *, if_match: int | None, summary: str | None) -> dict[str, Any]:
        await self._draft(who, agent_id)
        target = await self.repo.get_version(who.workspace_id, agent_id, version)
        if not target:
            raise AgentError('version_not_found', 'Versão não encontrada.')

        def build(draft, at):
            if draft['published'] and not changed_fields(draft['published'], target):
                raise AgentError('no_changes', 'A versão selecionada já é a publicada.')
            return plan_publication(draft, target, actor=who.user_id, at=at, summary=summary, restored_from=version, replace_draft=True)
        return await self._publish(who, agent_id, build, if_match)

    # history
    async def versions(self, who: Identity, agent_id: str, *, limit: int, cursor: str | None) -> dict[str, Any]:
        draft = await self._draft(who, agent_id)
        current = draft['published']['version'] if draft['published'] else None
        rows = await self.repo.list_versions(who.workspace_id, 'agent_versions', {'agent_id': agent_id}, decode_cursor(cursor), limit + 1)
        return _page(rows, limit, lambda r: version_view(r, current, full=False), lambda r: encode_cursor(r['version']))

    async def version(self, who: Identity, agent_id: str, version: int) -> dict[str, Any]:
        draft = await self._draft(who, agent_id)
        row = await self.repo.get_version(who.workspace_id, agent_id, version)
        if not row:
            raise AgentError('version_not_found', 'Versão não encontrada.')
        return version_view(row, draft['published']['version'] if draft['published'] else None, full=True)

    async def diff(self, who: Identity, agent_id: str, a: int, b: int) -> dict[str, Any]:
        await self._draft(who, agent_id)
        old = await self.repo.get_version(who.workspace_id, agent_id, a)
        new = await self.repo.get_version(who.workspace_id, agent_id, b)
        if not old or not new:
            raise AgentError('version_not_found', 'Versão não encontrada.')
        changes = diff_versions(old, new)
        return {'agent_id': agent_id, 'from': a, 'to': b, 'identical': not changes, 'changes': changes}

    async def prompt_versions(self, who: Identity, prompt_id: str, *, limit: int, cursor: str | None) -> dict[str, Any]:
        if not await self.repo.get_agent_by_prompt(who.workspace_id, prompt_id):
            raise AgentError('prompt_not_found', 'Prompt não encontrado.')
        rows = await self.repo.list_versions(who.workspace_id, 'prompt_versions', {'prompt_id': prompt_id}, decode_cursor(cursor), limit + 1)
        return _page(rows, limit, lambda r: {**{k: r[k] for k in ('prompt_id', 'agent_id', 'version', 'instructions', 'created_by')},
                                             'created_at': iso(r['created_at'])}, lambda r: encode_cursor(r['version']))

    async def graph_versions(self, who: Identity, *, limit: int, cursor: str | None) -> dict[str, Any]:
        rows = await self.repo.list_versions(who.workspace_id, 'graph_versions', {}, decode_cursor(cursor), limit + 1)
        view = lambda r: {'id': str(r['version']), 'version': r['version'], 'nodes': r['nodes'], 'summary': r['summary'],
                          'agent_ids': r['trigger_agent_ids'], 'created_by': r['created_by'], 'created_at': iso(r['created_at'])}
        return _page(rows, limit, view, lambda r: encode_cursor(r['version']))
