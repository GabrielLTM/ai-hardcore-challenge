import shutil
from pathlib import Path
import pytest
from app.application.use_cases.ingest_curated import read_curated_corpus, ingest_curated
from knowledge_doubles import FakeGateway, MemoryRepository


CORPUS = Path(__file__).resolve().parents[2] / 'data/knowledge/getnet'


def test_real_manifest_has_valid_provenance_and_hashes():
    docs = read_curated_corpus(CORPUS)
    assert len(docs) == 10
    assert all(d['source_url'] and d['retrieved_on'] and d['text'] for d in docs)


@pytest.mark.asyncio
async def test_ingestion_is_idempotent_and_respects_workspace():
    model, repo = FakeGateway(), MemoryRepository()
    assert await ingest_curated(CORPUS, model, repo, 'embedding', 'one') == 10
    calls = model.calls
    assert await ingest_curated(CORPUS, model, repo, 'embedding', 'one') == 10
    assert model.calls == calls and repo.writes == 10
    await ingest_curated(CORPUS, model, repo, 'embedding', 'two')
    assert model.calls == 2 * calls


@pytest.mark.asyncio
async def test_curated_documents_become_traceable_chunks():
    repo = MemoryRepository()
    await ingest_curated(CORPUS, FakeGateway(), repo, 'embedding', 'one')
    chunks = repo.chunks[('one', '07-antecipacao')].values()
    assert len(chunks) >= 2  # sections are chunk boundaries
    assert {c['document_type'] for c in chunks} == {'curated_summary'}
    assert all(c['source_url'].startswith('https://') and c['retrieved_on'] == '2026-11-03' and c['limitations'] for c in chunks)
    assert repo.sources[('one', '07-antecipacao')]['status'] == 'indexed'


@pytest.mark.asyncio
async def test_editing_limitations_in_the_manifest_reindexes_without_changing_content(tmp_path):
    corpus = tmp_path / 'getnet'
    shutil.copytree(CORPUS, corpus)
    repo, model = MemoryRepository(), FakeGateway()
    await ingest_curated(corpus, model, repo, 'embedding', 'one')
    manifest = (corpus / 'manifest.json').read_text(encoding='utf-8')
    (corpus / 'manifest.json').write_text(manifest.replace('"limitations": []', '"limitations": ["Nova ressalva."]', 1), encoding='utf-8')
    await ingest_curated(corpus, model, repo, 'embedding', 'one')
    assert repo.writes == 11 and 'Nova ressalva.' in repo.chunks[('one', '01-visao-global')]['01-visao-global#0']['limitations']


@pytest.mark.asyncio
async def test_tampered_document_fails_without_indexing_it(tmp_path):
    corpus = tmp_path / 'getnet'
    shutil.copytree(CORPUS, corpus)
    path = corpus / 'documents/01-visao-global.md'
    path.write_text(path.read_text(encoding='utf-8') + '\nTrecho adulterado.\n', encoding='utf-8')
    repo = MemoryRepository()
    with pytest.raises(RuntimeError, match='01-visao-global'):
        await ingest_curated(corpus, FakeGateway(), repo, 'embedding', 'one')
    assert ('one', '01-visao-global') not in repo.chunks and len(repo.chunks) == 9
