"""Ranking metrics, retrieval dataset, embedding cache and budget, modes and report of evaluation.retrieval. No database needed."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `evaluation/` sits beside `src/`, which pytest's pythonpath does not cover

import pytest  # noqa: E402
from evaluation import retrieval  # noqa: E402
from evaluation.metrics import ndcg_at, recall_at, reciprocal_rank  # noqa: E402


# --- metrics ----------------------------------------------------------------------------------------------------

def test_relevant_source_in_second_position():
    ranked, relevant = ['a', 'b', 'c'], {'b': 2}
    assert reciprocal_rank(ranked, relevant) == 0.5 and recall_at(ranked, relevant, 1) == 0 and recall_at(ranked, relevant, 3) == 1


def test_repeated_source_counts_once_at_its_best_position():
    ranked = ['a', 'a', 'a', 'b']  # three chunks of a: b is the second source, not the fourth result
    assert reciprocal_rank(ranked, {'b': 2}) == 0.5 and recall_at(ranked, {'b': 2}, 2) == 1
    assert ndcg_at(['b', 'b'], {'b': 2}, 5) == 1


def test_no_relevant_source_scores_zero():
    assert (recall_at(['x', 'y'], {'a': 2}, 5), reciprocal_rank(['x', 'y'], {'a': 2}), ndcg_at(['x', 'y'], {'a': 2}, 5)) == (0, 0, 0)
    assert reciprocal_rank([], {'a': 2}) == 0


def test_graded_relevance_rewards_the_answering_source_first():
    relevant = {'a': 2, 'b': 1}
    assert ndcg_at(['a', 'b'], relevant, 5) == pytest.approx(1)
    assert ndcg_at(['b', 'a'], relevant, 5) == pytest.approx((1 + 2 / 1.5849625) / (2 + 1 / 1.5849625))
    assert recall_at(['b', 'x'], relevant, 1) == 0.5


# --- dataset ----------------------------------------------------------------------------------------------------

def test_the_official_page_counts_as_its_curated_summary():
    from evaluation.retrieval import score_query
    by_url = {'https://site.getnet.com.br/pix/': '04-pix-comercial'}
    results = [{'source_id': 'official-pix', 'source_url': 'https://site.getnet.com.br/pix/', 'heading': 'Pix'},
               {'source_id': '09-status-vendas', 'source_url': 'https://outra', 'heading': 'x'}]
    row = score_query({'id': 'q', 'lang': 'pt-BR', 'relevant': {'04-pix-comercial': 2}}, results, by_url)
    assert row['recall@1'] == 1 and row['mrr'] == 1


def test_dataset_v1_is_valid_against_the_manifest():
    cases = retrieval.load_retrieval_dataset()
    assert len(cases) >= 30 and {c['lang'] for c in cases} == {'pt-BR', 'en'}
    assert all(2 in c['relevant'].values() for c in cases)


def test_dataset_with_unknown_source_is_refused_naming_query_and_source(tmp_path):
    path = tmp_path / 'd.jsonl'
    path.write_text(json.dumps({'id': 'q-9', 'query': 'x', 'lang': 'pt-BR', 'relevant': {'99-inexistente': 2}}) + '\n')
    with pytest.raises(ValueError, match='q-9.*99-inexistente'):
        retrieval.load_retrieval_dataset(path)


# --- embeddings: budget and cache -------------------------------------------------------------------------------

class Gateway:
    def __init__(self): self.texts = []

    async def embed(self, *, model, texts):
        self.texts += texts
        return [[0.5] for _ in texts]


def test_embed_without_budget_is_refused(capsys):
    with pytest.raises(SystemExit) as exc:
        asyncio.run(retrieval.amain(['--embed']))
    assert exc.value.code == 2 and '--max-embedding-tokens' in capsys.readouterr().err


async def test_budget_below_the_estimate_is_refused_without_calling_the_provider():
    gateway = Gateway()
    with pytest.raises(ValueError, match='max-embedding-tokens'):
        await retrieval.query_embeddings(['consulta longa'], 'm', {}, gateway, max_tokens=5)
    assert gateway.texts == []


async def test_full_cache_never_calls_the_provider_and_keys_by_model():
    cache = {retrieval.cache_key('m', 'pix'): [1.0]}
    gateway = Gateway()
    assert await retrieval.query_embeddings(['pix'], 'm', cache, gateway, max_tokens=1) == {'pix': [1.0]}
    assert gateway.texts == []
    assert await retrieval.query_embeddings(['pix'], 'outro-modelo', cache) is None  # a new model needs --embed


async def test_missing_embeddings_are_bought_within_budget_and_cached():
    cache, gateway = {retrieval.cache_key('m', 'pix'): [1.0]}, Gateway()
    vectors = await retrieval.query_embeddings(['pix', 'boleto', 'boleto'], 'm', cache, gateway, max_tokens=100)
    assert gateway.texts == ['boleto'] and vectors == {'pix': [1.0], 'boleto': [0.5]}
    assert cache[retrieval.cache_key('m', 'boleto')] == [0.5]


# --- modes and report -------------------------------------------------------------------------------------------

CASES = [{'id': 'q1', 'query': 'pix', 'lang': 'pt-BR', 'relevant': {'pix': 2}, 'section': 'Prazo'},
         {'id': 'q2', 'query': 'smart', 'lang': 'en', 'relevant': {'smart': 2, 'pix': 1}}]


class Repo:
    """Vector finds smart first, text finds the query's own source first; hybrid alternates."""
    def __init__(self): self.calls = []

    async def hybrid_search(self, workspace_id, query, embedding, limit=5, mode='hybrid'):
        self.calls.append((mode, embedding))
        order = {'vector': ['smart', 'pix'], 'text': [query, 'outro'], 'hybrid': ['smart', query, 'pix']}[mode]
        return [{'source_id': s, 'heading': 'Prazo' if s == 'pix' else 'Outra'} for s in order]


async def test_without_vectors_only_the_text_mode_runs():
    repo = Repo()
    result = await retrieval.evaluate(repo, 'demo', CASES, None)
    assert list(result['modes']) == ['text'] and result['skipped_modes'] == ['vector', 'hybrid']
    assert {mode for mode, _ in repo.calls} == {'text'} and result['divergences'] == []
    q1 = result['modes']['text']['queries'][0]
    assert q1['mrr'] == 1 and q1['section_hit'] is True and result['modes']['text']['aggregate']['section_hit'] == 1


async def test_three_modes_report_divergences_and_render(tmp_path):
    result = await retrieval.evaluate(Repo(), 'demo', CASES, {'pix': [1.0], 'smart': [0.0]})
    assert list(result['modes']) == ['vector', 'text', 'hybrid']
    loses = {d['id']: d['hybrid_loses'] for d in result['divergences']}
    assert loses == {'q1': True, 'q2': False}  # q1: only text puts pix first; q2: hybrid ties vector, text misses pix
    report = {'started_at': '2026-11-24T12:00:00Z', 'embedding_model': 'm', 'index_versions': ['v1'], 'indexed_sources': 2,
              'dataset': {'path': 'retrieval_v1.jsonl', 'sha256': 'ab' * 32, 'queries': 2},
              'search': {'limit': 5, 'rrf_k': 60, 'workspace_id': 'demo'},
              'embeddings': {'from_cache': 2, 'generated': 0, 'note': ''}, 'results': result}
    json_path, md_path = retrieval.write_report(report, tmp_path)
    assert json_path.name == 'retrieval-20261124T120000Z.json' and json.loads(json_path.read_text())['dataset']['sha256'] == 'ab' * 32
    md = md_path.read_text()
    assert '| hybrid |' in md and '| text |' in md and 'q1 (híbrido perde)' in md and 'abababababab' in md and 'índice v1' in md
