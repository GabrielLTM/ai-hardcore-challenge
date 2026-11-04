"""Evidence ids and citation checking; pure rules, no I/O."""
import re
from collections.abc import Iterable

# Only 1-3 digits so years such as [2024] are not mistaken for evidence ids.
_CITATION = re.compile(r'\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]')


def cited_ids(text: str) -> list[int]:
    """Evidence numbers cited as [n] or [n, m], in order of first appearance."""
    found: list[int] = []
    for match in _CITATION.finditer(text or ''):
        for raw in match.group(1).split(','):
            number = int(raw)
            if number not in found:
                found.append(number)
    return found


def split_citations(text: str, retrieved: Iterable[int]) -> tuple[list[int], list[int]]:
    """Return (valid, invalid) citations: only ids actually retrieved in the run are valid."""
    known = set(retrieved)
    ids = cited_ids(text)
    return [n for n in ids if n in known], [n for n in ids if n not in known]


def evidence_kind(document_type: str | None) -> str:
    return 'curated' if (document_type or '').startswith('curated') else 'official'
