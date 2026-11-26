"""Offline retrieval evaluation: labelled queries against the curated corpus, per search mode (vector, text, hybrid RRF).

Text only (free, default): queries the repository directly, no HTTP, no agents, no model.
    python -m evaluation.retrieval
Vector and hybrid need query embeddings: read from evaluation/cache/query_embeddings.json; buying the missing ones spends tokens.
    python -m evaluation.retrieval --embed --max-embedding-tokens 5000
Needs the local Mongo with the curated corpus ingested and the search indexes. Reports: reports/retrieval-<timestamp>.{json,md}.
"""
import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from evaluation.metrics import dataset_digest, load_dataset, ndcg_at, recall_at, reciprocal_rank
from evaluation.report import REPORTS
from openai import AsyncOpenAI
from pymongo import AsyncMongoClient
from app.adapters.outbound.mongo.knowledge import MongoKnowledgeRepository  # noqa: E402 (metrics puts src/ on the path)
from app.adapters.outbound.openai.gateway import OpenAIModelGateway  # noqa: E402
from app.bootstrap.settings import get_settings  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_DATASET = HERE.parent / 'tests' / 'evaluation' / 'retrieval_v1.jsonl'
MANIFEST = HERE.parents[1] / 'data' / 'knowledge' / 'getnet' / 'manifest.json'
CACHE = HERE / 'cache' / 'query_embeddings.json'
MODES = ('vector', 'text', 'hybrid')
LIMIT = 5
METRICS = ('recall@1', 'recall@3', 'recall@5', 'mrr', 'ndcg@5', 'section_hit')


def load_retrieval_dataset(path: Path = DEFAULT_DATASET, manifest: Path = MANIFEST) -> list[dict]:
    known = {d['id'] for d in json.loads(manifest.read_text(encoding='utf-8'))['documents']}
    cases = load_dataset(path)
    for case in cases:
        for source, grade in case['relevant'].items():
            if source not in known:
                raise ValueError(f"Consulta {case['id']}: a fonte {source} não existe no manifesto da base curada.")
            if grade not in (1, 2):
                raise ValueError(f"Consulta {case['id']}: relevância de {source} deve ser 1 ou 2.")
    return cases


def cache_key(model: str, query: str) -> str:
    return f"{model}:{hashlib.sha256(query.encode('utf-8')).hexdigest()}"


async def query_embeddings(queries, model, cache, gateway=None, max_tokens=None) -> dict | None:
    """Vectors from the cache; missing ones are bought only with a gateway and a budget. None: vector modes must be skipped."""
    missing = [q for q in dict.fromkeys(queries) if cache_key(model, q) not in cache]
    if missing:
        if gateway is None:
            return None
        estimate = sum(len(q.encode('utf-8')) for q in missing)  # upper bound: every token covers at least one byte
        if estimate > max_tokens:
            raise ValueError(f'{len(missing)} consultas sem cache estimam até {estimate} tokens, acima de --max-embedding-tokens {max_tokens}.')
        vectors = await gateway.embed(model=model, texts=missing)
        cache.update({cache_key(model, q): v for q, v in zip(missing, vectors)})
    return {q: cache[cache_key(model, q)] for q in queries}


def document_by_url(manifest: Path = MANIFEST) -> dict[str, str]:
    """Each curated summary and the official page it was written from share `source_url`: both are the same source."""
    return {d['source_url']: d['id'] for d in json.loads(manifest.read_text(encoding='utf-8'))['documents'] if d.get('source_url')}


def score_query(case: dict, results: list[dict], by_url: dict[str, str] | None = None) -> dict:
    by_url = document_by_url() if by_url is None else by_url
    ranked, relevant = [by_url.get(r.get('source_url'), r['source_id']) for r in results], case['relevant']
    answering = next((r for r in results if relevant.get(r['source_id']) == 2), None)  # sections name the summary's headings
    return {'id': case['id'], 'lang': case['lang'], 'sources': list(dict.fromkeys(ranked)),
            'recall@1': recall_at(ranked, relevant, 1), 'recall@3': recall_at(ranked, relevant, 3),
            'recall@5': recall_at(ranked, relevant, 5), 'mrr': reciprocal_rank(ranked, relevant), 'ndcg@5': ndcg_at(ranked, relevant, 5),
            # section of the best-ranked chunk of an answering source; None when the dataset does not name one
            'section_hit': None if 'section' not in case else bool(answering and answering.get('heading') == case['section'])}


def mean(rows: list[dict], metric: str) -> float | None:
    values = [r[metric] for r in rows if r[metric] is not None]
    return sum(values) / len(values) if values else None


async def evaluate(repo, workspace_id: str, cases: list[dict], vectors: dict | None, limit: int = LIMIT) -> dict:
    modes = MODES if vectors else ('text',)
    by_url = document_by_url()
    by_mode = {}
    for mode in modes:
        rows = [score_query(c, await repo.hybrid_search(workspace_id, c['query'], vectors and vectors[c['query']], limit=limit, mode=mode), by_url)
                for c in cases]
        by_mode[mode] = {'aggregate': {m: mean(rows, m) for m in METRICS}, 'queries': rows}
    divergences = []
    if len(modes) > 1:
        for i, case in enumerate(cases):
            row = {m: by_mode[m]['queries'][i] for m in modes}
            if len({(r['mrr'], r['ndcg@5']) for r in row.values()}) > 1:
                best_single = max(row['vector']['ndcg@5'], row['text']['ndcg@5'])
                divergences.append({'id': case['id'], 'query': case['query'], 'hybrid_loses': row['hybrid']['ndcg@5'] < best_single,
                                    **{m: {'mrr': r['mrr'], 'ndcg@5': r['ndcg@5'], 'top': r['sources'][:3]} for m, r in row.items()}})
    return {'modes': by_mode, 'skipped_modes': [m for m in MODES if m not in modes], 'divergences': divergences}


# --- report ---------------------------------------------------------------------------------------------------

def _num(value) -> str: return '-' if value is None else f'{value:.2f}'
def _versions(v: list) -> str: return f'índice {v[0]}' if len(v) == 1 else f'{len(v)} versões de índice (lista no JSON)'


def render_markdown(report: dict) -> str:
    modes = report['results']['modes']
    lines = [f"# Avaliação de recuperação: {report['started_at']}", '',
             f"Dataset `{report['dataset']['path']}` ({report['dataset']['queries']} consultas, sha256 `{report['dataset']['sha256'][:12]}`). "
             f"Modelo de embedding `{report['embedding_model']}`, {_versions(report['index_versions'])}, "
             f"limite {report['search']['limit']}, RRF k={report['search']['rrf_k']}.", '']
    if report['results']['skipped_modes']:
        lines += [f"Modos pulados: {', '.join(report['results']['skipped_modes'])} ({report['embeddings']['note']}).", '']
    lines += ['## Métricas por modo', '', '| modo | ' + ' | '.join(METRICS) + ' |', '|---' * (len(METRICS) + 1) + '|']
    lines += [f'| {mode} | ' + ' | '.join(_num(data['aggregate'][m]) for m in METRICS) + ' |' for mode, data in modes.items()]
    lines += ['', '## Por consulta (MRR / nDCG@5)', '', '| consulta | idioma | ' + ' | '.join(modes) + ' |', '|---' * (len(modes) + 2) + '|']
    for i, row in enumerate(next(iter(modes.values()))['queries']):
        cells = [f"{_num(d['queries'][i]['mrr'])} / {_num(d['queries'][i]['ndcg@5'])}" for d in modes.values()]
        lines.append(f"| {row['id']} | {row['lang']} | " + ' | '.join(cells) + ' |')
    if len(modes) > 1:
        lines += ['', '## Divergências entre modos', '']
        lines += [f"- {d['id']}{' (híbrido perde)' if d['hybrid_loses'] else ''}: "
                  + '; '.join(f"{m} {', '.join(d[m]['top']) or '-'}" for m in modes) for d in report['results']['divergences']] or ['Nenhuma.']
    lines += ['', 'Base de 10 documentos e dataset rotulado pelo autor: os números comparam os modos entre si, não medem a busca '
              'em produção. Veja docs/avaliacao.md.', '']
    return '\n'.join(lines)


def write_report(report: dict, out_dir: Path = REPORTS) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = 'retrieval-' + report['started_at'].replace('-', '').replace(':', '')
    json_path, md_path = out_dir / f'{stem}.json', out_dir / f'{stem}.md'
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    md_path.write_text(render_markdown(report), encoding='utf-8')
    return json_path, md_path


# --- command line ---------------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='python -m evaluation.retrieval', description=__doc__.split('\n')[0])
    p.add_argument('--embed', action='store_true', help='gera os embeddings que faltam no cache (gasta tokens); exige --max-embedding-tokens')
    p.add_argument('--max-embedding-tokens', type=int, help='teto de tokens dos embeddings das consultas')
    p.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    p.add_argument('--cache', type=Path, default=CACHE)
    p.add_argument('--out-dir', type=Path, default=REPORTS)
    return p


async def amain(argv=None, gateway=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.embed and not args.max_embedding_tokens:
        parser.error('--embed exige --max-embedding-tokens')
    try:
        cases = load_retrieval_dataset(args.dataset)
    except ValueError as exc:
        print(f'erro: {exc}', file=sys.stderr)
        return 2
    settings = get_settings()
    model = settings.embedding_model
    cache = json.loads(args.cache.read_text(encoding='utf-8')) if args.cache.exists() else {}
    openai = None
    if args.embed and gateway is None:
        if not settings.openai_api_key:
            print('erro: configure OPENAI_API_KEY para gerar embeddings.', file=sys.stderr)
            return 2
        openai = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        gateway = OpenAIModelGateway(openai)
    cached = sum(cache_key(model, c['query']) in cache for c in cases)
    try:
        vectors = await query_embeddings([c['query'] for c in cases], model, cache, gateway if args.embed else None, args.max_embedding_tokens)
    except ValueError as exc:
        print(f'erro: {exc}', file=sys.stderr)
        return 2
    finally:
        if openai:
            await openai.close()
    if vectors and cached < len(cases):
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        args.cache.write_text(json.dumps(cache) + '\n', encoding='utf-8')
    note = (f'cache cobre {cached} de {len(cases)} consultas; rode com --embed e --max-embedding-tokens' if vectors is None
            else f'{cached} do cache, {len(cases) - cached} gerados')
    if vectors is None:
        print(f'Só o modo textual: vetorial e híbrido pulados ({note}).')

    client = AsyncMongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    try:
        repo = MongoKnowledgeRepository(client[settings.mongo_database], model)
        manifest_ids = {d['id'] for d in json.loads(MANIFEST.read_text(encoding='utf-8'))['documents']}
        versions = {s['source_id']: s.get('index_version') for s in await repo.list_sources(settings.demo_workspace_id)
                    if s['source_id'] in manifest_ids and s.get('index_version')}
        if not versions:
            print('erro: a base curada não está indexada neste Mongo (rode ingest_curated).', file=sys.stderr)
            return 2
        started = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        results = await evaluate(repo, settings.demo_workspace_id, cases, vectors)
    except Exception as exc:  # Mongo down or search indexes missing: an environment error, not a result
        print(f'erro: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    finally:
        await client.close()
    report = {'started_at': started, 'embedding_model': model, 'index_versions': sorted(set(versions.values())),
              'indexed_sources': len(versions),
              'dataset': {'path': str(args.dataset.name), 'sha256': dataset_digest(args.dataset), 'queries': len(cases)},
              'search': {'limit': LIMIT, 'rrf_k': 60, 'workspace_id': settings.demo_workspace_id},
              'embeddings': {'from_cache': cached, 'generated': 0 if vectors is None else len(cases) - cached, 'note': note},
              'results': results}
    json_path, md_path = write_report(report, args.out_dir)
    for mode, data in results['modes'].items():
        print(f"{mode}: " + ', '.join(f'{m} {_num(v)}' for m, v in data['aggregate'].items()))
    print(f'Relatório: {json_path} e {md_path}')
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == '__main__':
    main()
