"""Phased ingestion of curated and official sources: collect, extract, chunk, embed, index.

A source that fails in a phase is recorded and dropped from the next ones; the last valid indexed
version is only replaced by the final `index` write, which is atomic in the repository.
"""
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from app.application.ports.knowledge import SourceError
from app.application.use_cases.knowledge_catalog import parse_curated, read_curated_text
from app.domain.knowledge import CURATED, build_chunks, embedding_input, index_version, processing_version, sha256

MIN_TEXT_CHARS = 200
EMBED_BATCH = 64
FAILURE_PREFIX = {'embed': 'Falha ao gerar embeddings', 'index': 'Falha ao gravar o índice'}
Report = Callable[..., Awaitable[None]]


async def _ignore(*args, **fields):
    return None


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class IngestionPipeline:
    def __init__(self, *, repository, gateway, embedding_model: str, curated_root: Path, fetcher=None,
                 extract_html=None, dimensions: int | None = None, max_chars: int = 1200, overlap: int = 150):
        self.repository, self.gateway, self.fetcher, self.extract_html = repository, gateway, fetcher, extract_html
        self.embedding_model, self.curated_root, self.dimensions = embedding_model, curated_root, dimensions
        self.max_chars, self.overlap = max_chars, overlap
        self.processing = processing_version(max_chars, overlap)

    async def run(self, workspace_id: str, job_id: str | None, catalog: dict, source_ids: list[str],
                  report: Report = _ignore) -> list[dict]:
        items = {sid: {'id': sid, 'ws': workspace_id, 'job_id': job_id, 'source': catalog.get(sid)} for sid in source_ids}
        results: dict[str, dict] = {}
        steps = (('collect', self._collect), ('extract', self._extract), ('chunk', self._chunk),
                 ('embed', self._embed), ('index', self._index))
        for phase, step in steps:
            todo = [sid for sid in items if sid not in results]
            if not todo:
                await report(phase, 'skipped')
                continue
            await report(phase, 'running')
            failed = 0
            for sid in todo:
                try:
                    outcome = await step(items[sid])
                except Exception as exc:
                    failed += 1
                    error = self._error(phase, exc)
                    results[sid] = {'source_id': sid, 'outcome': 'failed', 'error': error}
                    await self.repository.record_failure(workspace_id, sid, job_id, error)
                else:
                    if outcome:
                        results[sid] = outcome
            status = 'succeeded' if not failed else 'failed' if failed == len(todo) else 'partial'
            await report(phase, status, processed=len(todo) - failed, failed=failed)
        return [results[sid] for sid in items]

    @staticmethod
    def _error(phase: str, exc: Exception) -> dict:
        if isinstance(exc, SourceError):
            return {'phase': phase, 'code': exc.code, 'message': exc.message}
        if isinstance(exc, ValueError) and phase in ('collect', 'extract'):
            return {'phase': phase, 'code': 'invalid_document', 'message': str(exc)}
        code = {'embed': 'embedding_failed', 'index': 'index_failed'}.get(phase, 'internal')
        return {'phase': phase, 'code': code,
                'message': f"{FAILURE_PREFIX.get(phase, 'Falha inesperada')} ({type(exc).__name__})."}

    async def _collect(self, it):
        source = it['source']
        if source is None:
            raise SourceError('unknown_source', 'A fonte não está mais no catálogo.')
        if source['document_type'] == CURATED:
            it['raw'], it['retrieved_on'] = read_curated_text(self.curated_root, source), source['retrieved_on']
        else:
            it['raw'], it['retrieved_on'] = await self.fetcher.fetch(source['source_url']), _today()

    async def _extract(self, it):
        source = it['source']
        if source['document_type'] == CURATED:
            text = parse_curated(it['raw'], source)['text']
        else:
            text = self.extract_html(it['raw'].body)
        if len(text) < MIN_TEXT_CHARS:
            raise SourceError('empty', 'A página não trouxe conteúdo textual útil (pode depender de JavaScript).')
        it['text'], it['digest'] = text, sha256(text)
        it['version'] = index_version(self.processing, it['digest'], source)
        if await self.repository.is_current(it['ws'], it['id'], it['version'], self.embedding_model):
            await self.repository.record_unchanged(it['ws'], it['id'], it['job_id'])
            return {'source_id': it['id'], 'outcome': 'already_current', 'index_version': it['version']}

    async def _chunk(self, it):
        source = {**it['source'], 'retrieved_on': it['retrieved_on']}
        it['chunks'] = build_chunks(source, it['text'], it['digest'], max_chars=self.max_chars,
                                    overlap=self.overlap, embedding_model=self.embedding_model)
        if not it['chunks']:
            raise SourceError('empty', 'Nenhum trecho foi gerado a partir do conteúdo.')

    async def _embed(self, it):
        if self.gateway is None:
            raise SourceError('embedding_unavailable', 'Configure OPENAI_API_KEY para gerar embeddings.')
        inputs, vectors = [embedding_input(c) for c in it['chunks']], []
        for start in range(0, len(inputs), EMBED_BATCH):
            vectors += await self.gateway.embed(model=self.embedding_model, texts=inputs[start:start + EMBED_BATCH])
        if len(vectors) != len(inputs) or any(self.dimensions and len(v) != self.dimensions for v in vectors):
            raise SourceError('embedding_failed', 'O modelo devolveu embeddings com quantidade ou dimensão inesperada.')
        for chunk, vector in zip(it['chunks'], vectors):
            chunk['embedding'] = vector

    async def _index(self, it):
        source = it['source']
        record = {**{k: source.get(k) for k in ('document_type', 'title', 'source_url', 'publisher', 'region',
                                                'language', 'tags', 'limitations')},
                  'retrieved_on': it['retrieved_on'], 'content_sha256': it['digest'],
                  'processing_version': self.processing, 'index_version': it['version'],
                  'embedding_model': self.embedding_model, 'chunk_count': len(it['chunks'])}
        await self.repository.replace_source(it['ws'], it['id'], record, it['chunks'], it['job_id'])
        return {'source_id': it['id'], 'outcome': 'updated', 'chunks': len(it['chunks']), 'index_version': it['version']}
