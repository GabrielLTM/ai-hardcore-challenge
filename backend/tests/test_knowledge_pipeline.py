import json
from pathlib import Path
import httpx
import pytest
from app.application.use_cases.knowledge_catalog import load_catalog, official_sources
from knowledge_doubles import LONG, FakeGateway, MemoryRepository, official, page, pipeline, site

WS = 'demo'
SOURCE = official()
CATALOG = {SOURCE['id']: SOURCE}
HTML = {'content-type': 'text/html; charset=utf-8'}
PAGE = page(('Antecipação', LONG), ('Pix', LONG + 'Pix é instantâneo.'))


async def run(pipe, report=None, ids=None):
    kwargs = {'report': report} if report else {}
    return await pipe.run(WS, 'job-1', CATALOG, ids or [SOURCE['id']], **kwargs)


@pytest.mark.asyncio
async def test_official_page_becomes_chunks_with_provenance():
    repo = MemoryRepository()
    [result] = await run(pipeline(repo, site(PAGE)))
    assert result['outcome'] == 'updated' and result['chunks'] == 2  # one per section; the bare page title has no body
    chunks = list(repo.chunks[(WS, SOURCE['id'])].values())
    assert [c['chunk_id'] for c in chunks] == [f"{SOURCE['id']}#{i}" for i in range(2)]
    assert {c['source_url'] for c in chunks} == {SOURCE['source_url']}
    assert {c['document_type'] for c in chunks} == {'official_page'}
    assert all(c['limitations'] == ['Limite editorial.'] and c['embedding_model'] == 'embedding' for c in chunks)
    assert all(len(c['chunk_sha256']) == 64 and len(c['embedding']) == 2 and c['retrieved_on'] for c in chunks)
    assert 'Menu' not in ''.join(c['text'] for c in chunks) and 'Rodapé' not in ''.join(c['text'] for c in chunks)
    record = repo.sources[(WS, SOURCE['id'])]
    assert record['chunk_count'] == 2 and record['index_version'] == result['index_version']


@pytest.mark.asyncio
async def test_reprocessing_same_content_and_version_is_already_current():
    repo, gateway = MemoryRepository(), FakeGateway()
    pipe = pipeline(repo, site(PAGE), gateway)
    await run(pipe)
    calls = gateway.calls
    [again] = await run(pipe)
    assert again['outcome'] == 'already_current' and gateway.calls == calls and repo.writes == 1
    assert len(repo.chunks[(WS, SOURCE['id'])]) == 2


@pytest.mark.asyncio
async def test_new_content_new_chunking_or_new_model_reindexes():
    repo = MemoryRepository()
    await run(pipeline(repo, site(PAGE)))
    first = repo.sources[(WS, SOURCE['id'])]['index_version']
    [changed] = await run(pipeline(repo, site(page(('Antecipação', LONG + 'Condição nova.')))))
    assert changed['outcome'] == 'updated' and changed['index_version'] != first and len(repo.chunks[(WS, SOURCE['id'])]) == 1
    [rechunked] = await run(pipeline(repo, site(page(('Antecipação', LONG + 'Condição nova.'))), max_chars=300, overlap=30))
    assert rechunked['outcome'] == 'updated'
    pipe = pipeline(repo, site(page(('Antecipação', LONG + 'Condição nova.'))), max_chars=300, overlap=30)
    pipe.embedding_model = 'outro-modelo'
    assert (await run(pipe))[0]['outcome'] == 'updated'


@pytest.mark.asyncio
async def test_failed_collection_keeps_the_last_valid_version():
    repo = MemoryRepository()
    await run(pipeline(repo, site(PAGE)))
    before = (dict(repo.sources[(WS, SOURCE['id'])]), dict(repo.chunks[(WS, SOURCE['id'])]))
    [result] = await run(pipeline(repo, site(lambda r: httpx.Response(500, headers=HTML))))
    assert result['outcome'] == 'failed' and result['error']['phase'] == 'collect' and result['error']['code'] == 'unreachable'
    after = repo.sources[(WS, SOURCE['id'])]
    assert after['status'] == 'stale'
    assert {k: v for k, v in after.items() if k != 'status'} == {k: v for k, v in before[0].items() if k != 'status'}
    assert repo.chunks[(WS, SOURCE['id'])] == before[1] and repo.writes == 1


@pytest.mark.asyncio
async def test_empty_page_fails_as_empty_and_never_indexes():
    repo = MemoryRepository()
    [result] = await run(pipeline(repo, site('<html><body><nav>Menu</nav><p>Oi</p></body></html>')))
    assert result['error'] == {'phase': 'extract', 'code': 'empty',
                               'message': 'A página não trouxe conteúdo textual útil (pode depender de JavaScript).'}
    assert repo.sources[(WS, SOURCE['id'])]['status'] == 'failed' and repo.writes == 0


@pytest.mark.asyncio
async def test_empty_page_does_not_overwrite_a_valid_version():
    repo = MemoryRepository()
    await run(pipeline(repo, site(PAGE)))
    [result] = await run(pipeline(repo, site('<html><body><p>fora do ar</p></body></html>')))
    assert result['error']['code'] == 'empty' and repo.sources[(WS, SOURCE['id'])]['status'] == 'stale'
    assert len(repo.chunks[(WS, SOURCE['id'])]) == 2


@pytest.mark.asyncio
async def test_embedding_failure_is_reported_by_phase_without_leaking_the_provider_message():
    repo = MemoryRepository()
    gateway = FakeGateway(error=RuntimeError('Incorrect API key provided: sk-secret-123'))
    [result] = await run(pipeline(repo, site(PAGE), gateway))
    assert result['error']['phase'] == 'embed' and result['error']['code'] == 'embedding_failed'
    assert 'sk-secret' not in result['error']['message'] and repo.writes == 0


@pytest.mark.asyncio
async def test_unexpected_embedding_dimension_is_rejected_and_missing_key_is_explicit():
    repo = MemoryRepository()
    [wrong] = await run(pipeline(repo, site(PAGE), FakeGateway(dimensions=5), dimensions=2))
    assert wrong['error']['code'] == 'embedding_failed' and repo.writes == 0
    [missing] = await run(pipeline(repo, site(PAGE), gateway=None))
    assert missing['error']['code'] == 'embedding_unavailable' and 'OPENAI_API_KEY' in missing['error']['message']


@pytest.mark.asyncio
async def test_unknown_source_and_mixed_results_are_per_source():
    repo = MemoryRepository()
    results = await run(pipeline(repo, site(PAGE)), ids=['official-x', 'gone'])
    assert [r['outcome'] for r in results] == ['updated', 'failed']
    assert results[1]['error']['code'] == 'unknown_source' and results[1]['error']['phase'] == 'collect'


@pytest.mark.asyncio
async def test_phase_reports_are_real_states_and_skip_when_nothing_is_left():
    events = []

    async def report(phase, status, **counts):
        events.append((phase, status, counts))

    repo = MemoryRepository()
    pipe = pipeline(repo, site(PAGE))
    await run(pipe, report)
    assert [(p, s) for p, s, _ in events] == [(p, s) for p in ('collect', 'extract', 'chunk', 'embed', 'index')
                                              for s in ('running', 'succeeded')]
    assert events[1][2] == {'processed': 1, 'failed': 0}
    events.clear()
    await run(pipe, report)
    assert [(p, s) for p, s, _ in events] == [('collect', 'running'), ('collect', 'succeeded'), ('extract', 'running'),
                                              ('extract', 'succeeded'), ('chunk', 'skipped'), ('embed', 'skipped'),
                                              ('index', 'skipped')]


def test_official_url_list_is_a_valid_allowlist_and_catalog_has_both_kinds():
    data_dir = Path(__file__).resolve().parents[2] / 'data/knowledge'
    hosts, sources = official_sources(data_dir / 'sources.json')
    assert 'www.getnet.net' in hosts and 'site.getnet.com.br' in hosts
    assert any(s['source_url'] == 'https://www.getnet.net/en/' for s in sources)
    assert all(s['source_url'].startswith('https://') and s['limitations'] is not None for s in sources)
    catalog = load_catalog(data_dir)
    assert len(catalog) == 20 and {s['document_type'] for s in catalog.values()} == {'curated_summary', 'official_page'}


def test_a_source_url_outside_the_allowlist_is_rejected_when_loading(tmp_path):
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps({'allowed_domains': ['site.getnet.com.br'], 'sources': [
        {'id': 'x', 'title': 'x', 'source_url': 'https://evil.example.com/'}]}))
    with pytest.raises(ValueError, match='evil.example.com'):
        official_sources(path)
