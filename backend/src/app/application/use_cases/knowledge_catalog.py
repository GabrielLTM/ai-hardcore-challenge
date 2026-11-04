"""Source catalog: curated manifest documents plus the allowlisted official URLs (sources.json)."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from app.domain.knowledge import CURATED, OFFICIAL, sha256


def _document_path(root: Path, metadata: dict) -> Path:
    root = root.resolve()
    path = (root / metadata['path']).resolve()
    if not path.is_relative_to(root / 'documents'):
        raise ValueError('O documento sai do diretório do corpus curado.')
    return path


def read_curated_text(root: Path, metadata: dict) -> str:
    return _document_path(root, metadata).read_text(encoding='utf-8')


def parse_curated(text: str, metadata: dict) -> dict:
    """Split front matter, title and body; the body hash must match the manifest."""
    if not text.startswith('---\n'):
        raise ValueError('Documento sem metadados.')
    _, remainder = text[4:].split('\n---\n\n', 1)
    title, body = remainder.split('\n\n', 1)
    if not title.startswith('# '):
        raise ValueError('Documento sem título.')
    if sha256(body) != metadata['content_sha256']:
        raise ValueError(f"O hash do documento diverge do manifesto: {metadata['id']}")
    return {**metadata, 'text': body, 'title': title[2:]}


def read_curated_corpus(root: Path) -> list[dict]:
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    return [parse_curated(read_curated_text(root, m), m) for m in manifest['documents']]


def curated_sources(root: Path) -> list[dict]:
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    return [{**m, 'document_type': CURATED} for m in manifest['documents']]


def official_sources(path: Path) -> tuple[list[str], list[dict]]:
    """Returns (allowed hosts, sources); every source URL must be https on an allowed host."""
    data = json.loads(path.read_text(encoding='utf-8'))
    hosts = [h.lower() for h in data['allowed_domains']]
    sources = []
    for item in data['sources']:
        parts = urlsplit(item['source_url'])
        if parts.scheme != 'https' or (parts.hostname or '').lower() not in hosts:
            raise ValueError(f"URL fora da lista de domínios oficiais: {item['source_url']}")
        sources.append({'publisher': 'Getnet', 'limitations': [], **item, 'document_type': OFFICIAL})
    return hosts, sources


def load_catalog(data_dir: Path) -> dict[str, dict]:
    """Curated root is data_dir/getnet; official URLs are data_dir/sources.json."""
    sources = [*curated_sources(data_dir / 'getnet'), *official_sources(data_dir / 'sources.json')[1]]
    return {s['id']: s for s in sources}
