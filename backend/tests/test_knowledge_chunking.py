import pytest
from app.domain.knowledge import build_chunks, chunk_markdown, index_version, processing_version, sha256

DOC = '''Introdução curta.

## Antecipação

Primeiro parágrafo da antecipação.

Segundo parágrafo da antecipação.

## Pix

Texto do Pix.
'''


def test_sections_are_chunk_boundaries_and_keep_their_heading():
    chunks = chunk_markdown(DOC, 'Título do documento')
    assert [(c['heading'], c['text'].split('\n')[0]) for c in chunks] == [
        ('Título do documento', 'Introdução curta.'),
        ('Antecipação', 'Primeiro parágrafo da antecipação.'),
        ('Pix', 'Texto do Pix.')]


def test_long_sections_split_within_limit_with_overlap():
    words = ' '.join(f'palavra{i}' for i in range(300))
    chunks = chunk_markdown(f'## Longo\n\n{words}', 'T', max_chars=200, overlap=40)
    assert len(chunks) > 3
    assert all(len(c['text']) <= 200 for c in chunks)
    for previous, current in zip(chunks, chunks[1:]):
        shared = current['text'].split('\n\n')[0]  # the carried-over tail starts the next chunk
        assert shared and shared in previous['text']


def test_paragraph_packing_respects_the_limit_and_zero_overlap_means_no_repeat():
    chunks = chunk_markdown('## S\n\n' + '\n\n'.join(c * 90 for c in 'abcd'), 'T', max_chars=200, overlap=0)
    assert [len(c['text']) for c in chunks] == [182, 182]


def test_repeated_blocks_are_dropped_and_invalid_overlap_rejected():
    assert len(chunk_markdown('## A\n\nigual\n\n## B\n\nigual', 'T')) == 1
    with pytest.raises(ValueError):
        chunk_markdown('texto', 'T', max_chars=100, overlap=60)


def test_chunk_records_carry_provenance_hashes_and_versions():
    source = {'id': 'doc', 'title': 'Doc', 'source_url': 'https://x/', 'document_type': 'official_page', 'publisher': 'G',
              'region': 'BR', 'language': 'pt-BR', 'retrieved_on': '2026-11-03', 'limitations': ['L'], 'tags': []}
    chunks = build_chunks(source, DOC, sha256(DOC), max_chars=1200, overlap=150, embedding_model='m')
    assert [c['chunk_id'] for c in chunks] == ['doc#0', 'doc#1', 'doc#2']
    first = chunks[0]
    assert first['chunk_sha256'] == sha256(first['text']) and first['content_sha256'] == sha256(DOC)
    assert first['source_url'] == 'https://x/' and first['limitations'] == ['L'] and first['embedding_model'] == 'm'
    assert first['index_version'].startswith(processing_version(1200, 150)) and len({c['index_version'] for c in chunks}) == 1


def test_index_version_changes_with_content_parameters_and_metadata():
    source = {'id': 'd', 'title': 'D', 'source_url': 'https://x/', 'limitations': []}
    base = index_version(processing_version(1200, 150), sha256('a'), source)
    assert base == index_version(processing_version(1200, 150), sha256('a'), source)
    assert base != index_version(processing_version(1200, 150), sha256('b'), source)
    assert base != index_version(processing_version(800, 150), sha256('a'), source)
    assert base != index_version(processing_version(1200, 150), sha256('a'), {**source, 'limitations': ['nova']})
