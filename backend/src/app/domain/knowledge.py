"""Pure knowledge rules: section chunking, hashes and index versions."""
import hashlib
import json
import re

PROCESSING_VERSION = 'p1'  # bump when extraction or chunking logic changes
PHASES = ('collect', 'extract', 'chunk', 'embed', 'index')
CURATED, OFFICIAL = 'curated_summary', 'official_page'
EMBEDDING_DIMENSIONS = {'text-embedding-3-small': 1536, 'text-embedding-3-large': 3072, 'text-embedding-ada-002': 1536}
_HEADING = re.compile(r'^#{1,6}\s+(.+?)\s*$')
_META_KEYS = ('title', 'source_url', 'document_type', 'language', 'region', 'limitations')


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def processing_version(max_chars: int, overlap: int) -> str:
    return f'{PROCESSING_VERSION}-c{max_chars}-o{overlap}'


def index_version(processing: str, digest: str, source: dict) -> str:
    """Changes with content, chunking parameters or editorial metadata (limitations, title, URL)."""
    meta = json.dumps({k: source.get(k) for k in _META_KEYS}, sort_keys=True, ensure_ascii=False)
    return f'{processing}:{sha256(digest + meta)[:12]}'


def _units(body: str, limit: int):
    for para in re.split(r'\n\s*\n', body):
        para = para.strip()
        while len(para) > limit:
            cut = para.rfind(' ', 0, limit)
            cut = cut if cut > 0 else limit
            yield para[:cut].strip()
            para = para[cut:].strip()
        if para:
            yield para


def _tail(text: str, overlap: int) -> str:
    if overlap == 0:
        return ''
    tail = text[-overlap:]
    space = tail.find(' ')
    return tail[space + 1:] if space != -1 and len(text) > overlap else tail


def _pack(body: str, max_chars: int, overlap: int) -> list[str]:
    chunks, current, tail = [], '', ''
    join = lambda: f'{tail}\n\n{current}' if tail else current
    for unit in _units(body, max_chars - overlap - 2):
        candidate = f'{current}\n\n{unit}' if current else unit
        if current and len(tail) + 2 + len(candidate) > max_chars:
            chunks.append(join())
            tail, current = _tail(current, overlap), unit
        else:
            current = candidate
    if current:
        chunks.append(join())
    return chunks


def chunk_markdown(markdown: str, title: str, max_chars: int = 1200, overlap: int = 150) -> list[dict]:
    """Split on headings, pack paragraphs up to max_chars, carry `overlap` chars into the next chunk of a section."""
    if not 0 <= overlap < max_chars // 2:
        raise ValueError('overlap must be smaller than half of max_chars')
    sections, heading, lines = [], title, []
    for line in markdown.splitlines():
        match = _HEADING.match(line)
        if match:
            sections.append((heading, '\n'.join(lines).strip()))
            heading, lines = match.group(1), []
        else:
            lines.append(line)
    sections.append((heading, '\n'.join(lines).strip()))
    chunks, seen = [], set()
    for heading, body in sections:
        for text in _pack(body, max_chars, overlap) if body else []:
            if text not in seen:  # repeated boilerplate blocks add nothing to retrieval
                seen.add(text)
                chunks.append({'heading': heading, 'text': text})
    return chunks


def build_chunks(source: dict, markdown: str, digest: str, *, max_chars: int, overlap: int, embedding_model: str) -> list[dict]:
    processing = processing_version(max_chars, overlap)
    version = index_version(processing, digest, source)
    meta = {k: source.get(k) for k in ('title', 'source_url', 'document_type', 'publisher', 'region', 'language',
                                       'retrieved_on', 'limitations', 'tags')}
    return [{**meta, 'chunk_id': f"{source['id']}#{i}", 'source_id': source['id'], 'chunk_index': i,
             'heading': c['heading'], 'text': c['text'], 'chunk_sha256': sha256(c['text']),
             'content_sha256': digest, 'processing_version': processing, 'index_version': version,
             'embedding_model': embedding_model}
            for i, c in enumerate(chunk_markdown(markdown, source['title'], max_chars, overlap))]


def embedding_input(chunk: dict) -> str:
    heading = '' if chunk['heading'] == chunk['title'] else f"\n{chunk['heading']}"
    return f"{chunk['title']}{heading}\n\n{chunk['text']}"
