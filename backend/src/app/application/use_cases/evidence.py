"""Per-run evidence ledger: numbers the retrieved passages and checks citations against them."""
from typing import Any
from app.domain.evidence import evidence_kind, split_citations

_PASSAGE_LIMIT = 2000
_EVENT_FIELDS = ('evidence_id', 'kind', 'title', 'url', 'retrieved_on')  # passage text stays out of events


class EvidenceLedger:
    def __init__(self):
        self._items: dict[str, dict[str, Any]] = {}

    def _add(self, key: str, **fields) -> dict[str, Any]:
        if key not in self._items:
            self._items[key] = {'evidence_id': len(self._items) + 1, **fields}
        return self._items[key]

    def add_passages(self, docs: list[dict]) -> list[dict]:
        """Register knowledge chunks; returns them with the evidence_id the model must cite."""
        out = []
        for d in docs:
            item = self._add(
                f"chunk:{d.get('chunk_id') or d.get('source_id')}", kind=evidence_kind(d.get('document_type')),
                chunk_id=d.get('chunk_id'), source_id=d.get('source_id'), title=d.get('title'), heading=d.get('heading'), url=d.get('source_url'),
                version=d.get('index_version'), retrieved_on=d.get('retrieved_on'),
                limitations=d.get('limitations') or [], document_type=d.get('document_type'),
                text=(d.get('text') or '')[:_PASSAGE_LIMIT])
            out.append({**d, 'evidence_id': item['evidence_id'], 'cite_as': f"[{item['evidence_id']}]"})
        return out

    def add_web_sources(self, sources: list[dict], retrieved_at: str | None) -> list[dict]:
        out = []
        for s in sources:
            item = self._add(f"web:{s.get('url') or s.get('title')}", kind='web', chunk_id=None, source_id=None,
                             title=s.get('title') or s.get('url'), heading=None, url=s.get('url'), version=None,
                             retrieved_on=retrieved_at, limitations=['Resultado de busca na web, sem validação da Getnet.'],
                             document_type='web_search', text='')
            out.append({**s, 'evidence_id': item['evidence_id'], 'cite_as': f"[{item['evidence_id']}]"})
        return out

    @property
    def retrieved(self) -> list[dict[str, Any]]:
        return list(self._items.values())

    def resolve(self, answer: str) -> tuple[list[dict[str, Any]], list[int]]:
        """Return (cited items, invalid citation numbers) for the final answer."""
        valid, invalid = split_citations(answer, (i['evidence_id'] for i in self._items.values()))
        by_id = {i['evidence_id']: i for i in self._items.values()}
        return [by_id[n] for n in valid], invalid


def event_view(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item.get(k) for k in _EVENT_FIELDS}
