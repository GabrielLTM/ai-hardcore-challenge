"""Reviewer verdict: schema, validation and the uncited-number detector. Pure rules, no I/O."""
import json
import re
from dataclasses import dataclass, field
from typing import Any

APPROVE, REVISE, SKIPPED, FAILED = 'aprovar', 'revisar', 'skipped', 'failed'
CRITERIA = ('fundamentacao', 'cobertura', 'limitacoes', 'seguranca')
MAX_ISSUES = 8

VERDICT_SCHEMA: dict[str, Any] = {
    'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'scores', 'issues'],
    'properties': {
        'verdict': {'type': 'string', 'enum': [APPROVE, REVISE]},
        'scores': {'type': 'object', 'additionalProperties': False, 'required': list(CRITERIA),
                   'properties': {c: {'type': 'integer', 'minimum': 0, 'maximum': 2} for c in CRITERIA}},
        'issues': {'type': 'array', 'items': {'type': 'string'}},
    },
}


class InvalidVerdict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: str
    scores: dict[str, int]
    issues: list[str] = field(default_factory=list)


def parse_verdict(text: str) -> Verdict:
    """Strict check of the reviewer output; anything off-schema raises InvalidVerdict."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise InvalidVerdict('saída não é JSON') from None
    if not isinstance(data, dict) or data.get('verdict') not in (APPROVE, REVISE):
        raise InvalidVerdict('veredito ausente ou inválido')
    scores = data.get('scores')
    if not isinstance(scores, dict) or set(scores) != set(CRITERIA) or \
            any(not isinstance(scores[c], int) or isinstance(scores[c], bool) or not 0 <= scores[c] <= 2 for c in CRITERIA):
        raise InvalidVerdict('notas ausentes ou fora da faixa 0-2')
    issues = data.get('issues', [])
    if not isinstance(issues, list) or not all(isinstance(i, str) for i in issues):
        raise InvalidVerdict('problemas devem ser uma lista de textos')
    return Verdict(data['verdict'], {c: scores[c] for c in CRITERIA}, issues[:MAX_ISSUES])


_CITATION = re.compile(r'\[\d{1,3}(?:\s*,\s*\d{1,3})*\]')
_NUMBER_CLAIM = re.compile(
    r'\d+(?:[.,]\d+)?\s*%'                                         # rates
    r'|R\$\s*\d'                                                   # money
    r'|\b\d{1,3}\s*(?:x|vezes|parcelas?|installments?|times)\b'    # installment counts
    r'|\b\d+\s*(?:dias?|days?|horas?|hours?|meses|months?|segundos?|seconds?)\b', re.I)


def number_claims(text: str) -> list[str]:
    """Uncited rates, money, installment counts and deadlines written as digits.

    A sentence carrying a citation marker ([2]) is grounded in a retrieved passage, so its numbers are not claims of the model;
    citation validity is checked separately. shortcut: numbers spelled out ("doze parcelas") are not seen, upgrade to a judge."""
    sentences = re.split(r'(?<=[.!?])\s+(?!\[\d)|\n+', text)  # a trailing [n] belongs to the sentence before it
    return [m.group(0) for s in sentences if not _CITATION.search(s) for m in _NUMBER_CLAIM.finditer(s)]
