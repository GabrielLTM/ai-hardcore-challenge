"""Pure rules for agent drafts, immutable versions, diffs and secrets."""
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

CONTENT_FIELDS = ('name', 'description', 'kind', 'model', 'tools', 'delegates', 'limits', 'enabled', 'instructions')
LABELS = {'name': 'nome', 'description': 'descrição', 'kind': 'tipo', 'model': 'modelo', 'tools': 'ferramentas',
          'delegates': 'agentes delegáveis', 'limits': 'limites', 'enabled': 'ativação', 'instructions': 'instruções'}
_SECRET = re.compile(r'sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?i:bearer)\s+[A-Za-z0-9._~+/-]{20,}')


class AgentError(Exception):
    """Business-rule failure; the HTTP adapter maps `code` to a status."""
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


class GraphConflict(Exception):
    """The workspace graph moved between validation and commit."""


class RevisionConflict(Exception):
    """The draft changed between reading and committing."""


@dataclass(frozen=True, slots=True)
class Publication:
    agent_id: str
    expected_revision: int
    version: dict[str, Any]          # new agent_versions document (the store adds workspace_id)
    prompt: dict[str, Any] | None    # new prompt_versions document, only when instructions changed
    draft_update: dict[str, Any] | None = None  # restore: draft content replaced in the same commit


def contains_secret(*texts: str) -> bool:
    return any(_SECRET.search(t) for t in texts)


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', ascii_name).strip('-')[:40] or 'agente'


def content(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {f: doc[f] for f in CONTENT_FIELDS}


def changed_fields(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    return [f for f in CONTENT_FIELDS if before.get(f) != after.get(f)]


def summarize(changed: list[str]) -> str:
    return 'Alterou: ' + ', '.join(LABELS[f] for f in changed)


def new_draft(agent_id: str, fields: Mapping[str, Any], actor: str, at: datetime) -> dict[str, Any]:
    return {'id': agent_id, 'prompt_id': f'prompt-{agent_id}', **content(fields), 'revision': 1, 'published': None,
            'created_at': at, 'created_by': actor, 'updated_at': at, 'updated_by': actor}


def plan_publication(draft: Mapping[str, Any], new: Mapping[str, Any], *, actor: str, at: datetime,
                     summary: str | None = None, restored_from: int | None = None, replace_draft: bool = False) -> Publication:
    """Build the next immutable agent version (and prompt version when the instructions changed)."""
    published = draft['published']
    prompt_version = published['prompt_version'] if published else 0
    prompt = None
    if not published or published['instructions'] != new['instructions']:
        prompt_version += 1
        prompt = {'prompt_id': draft['prompt_id'], 'agent_id': draft['id'], 'version': prompt_version,
                  'instructions': new['instructions'], 'created_by': actor, 'created_at': at}
    if summary is None:
        summary = ('Versão inicial' if not published
                   else f'Restaurada a partir da versão {restored_from}' if restored_from else summarize(changed_fields(published, new)))
    version = {'agent_id': draft['id'], 'prompt_id': draft['prompt_id'], 'version': (published['version'] if published else 0) + 1,
               'prompt_version': prompt_version, **content(new), 'summary': summary, 'created_by': actor, 'created_at': at}
    if restored_from:
        version['restored_from'] = restored_from
    return Publication(draft['id'], draft['revision'], version, prompt, content(new) if replace_draft else None)


def line_diff(before: str, after: str) -> list[dict[str, str]]:
    old, new = before.splitlines(), after.splitlines()
    lines: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == 'equal':
            lines += [{'op': 'equal', 'text': t} for t in old[i1:i2]]
        else:
            lines += [{'op': 'remove', 'text': t} for t in old[i1:i2]] + [{'op': 'add', 'text': t} for t in new[j1:j2]]
    return lines


def diff_versions(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Field-by-field readable comparison; lists carry added/removed, instructions carry a line diff."""
    changes = []
    for field in changed_fields(before, after):
        old, new = before.get(field), after.get(field)
        change: dict[str, Any] = {'field': field, 'label': LABELS[field], 'before': old, 'after': new}
        if isinstance(new, list):
            change['added'] = [x for x in new if x not in old]
            change['removed'] = [x for x in old if x not in new]
        if field == 'instructions':
            change['lines'] = line_diff(old, new)
        changes.append(change)
    return changes
