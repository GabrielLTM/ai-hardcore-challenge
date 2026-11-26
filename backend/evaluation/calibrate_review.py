"""Calibration of the Reviewer: does it flag known-bad answers and approve good ones?

Each case of `tests/evaluation/review_v1.jsonl` is a question, fixed evidence passages, an answer and the expected verdict
(`aprovar`/`revisar`) with the defect it carries. The Reviewer is called exactly as in a run (same instructions, input and
schema), without retrieval or agents, so only the judgment is measured.

Modes:
- simulated (default): an oracle model answers the expected verdict. It checks the plumbing and the report, not the judge.
- real (`--real`): the OpenAI model configured in OPENAI_MODEL with the seed's Reviewer instructions. Spends tokens;
  requires `--max-cases` and `--max-total-tokens`.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / 'src')
if _SRC not in sys.path:
    sys.path.append(_SRC)

from app.adapters.outbound.mongo.seed import REVIEWER_INSTRUCTIONS  # noqa: E402
from app.application.use_cases.orchestration.orchestrator import Orchestrator  # noqa: E402
from app.domain.identity import Identity  # noqa: E402
from app.domain.models import Agent, AgentKind, ModelEvent  # noqa: E402
from app.domain.review import CRITERIA  # noqa: E402
from evaluation.metrics import dataset_digest  # noqa: E402
from evaluation.report import REPORTS  # noqa: E402

DATASET = Path(__file__).resolve().parents[1] / 'tests' / 'evaluation' / 'review_v1.jsonl'
DEFECT_CRITERION = {'invented_number': 'fundamentacao', 'unsupported_citation': 'fundamentacao', 'invalid_citation': 'fundamentacao',
                    'ignored_part': 'cobertura', 'missing_limitation': 'limitacoes', 'leak': 'seguranca'}


def load_cases(path: Path = DATASET) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    for c in cases:
        if c.get('expect') not in ('aprovar', 'revisar') or not c.get('evidence') or not c.get('answer'):
            raise ValueError(f"caso inválido: {c.get('id')}")
        if c['expect'] == 'revisar' and c.get('defect') not in DEFECT_CRITERION:
            raise ValueError(f"caso {c['id']}: defeito desconhecido {c.get('defect')}")
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('O dataset tem ids repetidos.')
    return cases


class Oracle:
    """Simulated judge: answers the expected verdict, scoring the defect's criterion 0."""

    def __init__(self, cases: dict[str, dict]):
        self.by_answer = {c['answer']: c for c in cases.values()}

    async def stream(self, *, inputs, **_):
        content = inputs[0]['content']
        case = next(c for a, c in self.by_answer.items() if json.dumps(a, ensure_ascii=False)[1:-1] in content)
        scores = {c: 2 for c in CRITERIA}
        if case['expect'] == 'revisar':
            scores[DEFECT_CRITERION[case['defect']]] = 0
        text = json.dumps({'verdict': case['expect'], 'scores': scores, 'issues': [] if case['expect'] == 'aprovar' else [case['defect']]})
        yield ModelEvent('text_delta', text=text)
        yield ModelEvent('completed', data={'output': [], 'usage': {'input_tokens': len(content) // 4, 'output_tokens': len(text) // 4,
                                                                    'total_tokens': (len(content) + len(text)) // 4}})


async def judge_case(gateway, model: str, case: dict) -> dict:
    reviewer = Agent('reviewer', 'Revisor', '', REVIEWER_INSTRUCTIONS, AgentKind.REVIEWER, model)
    orch = Orchestrator(gateway, {'reviewer': reviewer}, {})
    orch.evidence.add_passages([{**e, 'chunk_id': f"{e['source_id']}#{i}", 'document_type': 'curated_summary'}
                                for i, e in enumerate(case['evidence'])])
    usage: dict = {}

    async def emit(kind, data):
        if kind == 'usage.recorded':
            usage.update(data.get('usage') or {})
    deadline = asyncio.get_running_loop().time() + 60
    result = await orch.judge(1, case['question'], case['answer'], emit, {'output_tokens': 0, 'data_tools': 0}, deadline)
    return {**result, 'tokens': usage.get('total_tokens') or 0}


def score(case: dict, result: dict) -> dict:
    verdict = result['verdict']
    out = {'id': case['id'], 'expect': case['expect'], 'defect': case.get('defect'), 'verdict': verdict,
           'scores': result.get('scores'), 'issues': result.get('issues'), 'tokens': result['tokens'], 'correct': verdict == case['expect']}
    if case['expect'] == 'revisar' and result.get('scores'):
        out['criterion_hit'] = result['scores'][DEFECT_CRITERION[case['defect']]] < 2  # flagged for the right reason
    return out


def summarize(results: list[dict]) -> dict:
    bad = [r for r in results if r['expect'] == 'revisar']
    good = [r for r in results if r['expect'] == 'aprovar']
    def rate(n, d): return n / d if d else None
    by_defect: dict[str, dict] = {}
    for r in bad:
        d = by_defect.setdefault(r['defect'], {'cases': 0, 'detected': 0, 'criterion_hit': 0})
        d['cases'] += 1
        d['detected'] += r['verdict'] == 'revisar'
        d['criterion_hit'] += bool(r.get('criterion_hit'))
    return {'cases': len(results), 'failed_reviews': sum(r['verdict'] == 'failed' for r in results),
            'recall_revisar': rate(sum(r['verdict'] == 'revisar' for r in bad), len(bad)),
            'specificity': rate(sum(r['verdict'] == 'aprovar' for r in good), len(good)),
            'criterion_hit_rate': rate(sum(bool(r.get('criterion_hit')) for r in bad), len(bad)),
            'by_defect': by_defect, 'tokens': sum(r['tokens'] for r in results)}


def render(report: dict) -> str:
    s = report['summary']
    pct = lambda v: '-' if v is None else f'{v:.0%}'  # noqa: E731
    lines = [f"# Calibração do Revisor ({report['mode']})", '',
             f"- Dataset: `{report['dataset']}` (sha256 `{report['sha256'][:12]}`), modelo `{report['model']}`",
             f"- Casos: {s['cases']}; falhas do Revisor: {s['failed_reviews']}; tokens: {s['tokens']}", '',
             f"- Detecção de defeitos (recall de `revisar`): {pct(s['recall_revisar'])}",
             f"- Aprovação das respostas corretas (especificidade): {pct(s['specificity'])}",
             f"- Defeito apontado no critério certo: {pct(s['criterion_hit_rate'])}", '']
    if report['mode'] == 'simulated':
        lines += ['> Modo simulado: o juiz é um oráculo que devolve o rótulo esperado. Mede o encanamento, não o Revisor.', '']
    lines += ['| Defeito | Casos | Detectados | Critério certo |', '|---|---|---|---|']
    lines += [f"| {d} | {v['cases']} | {v['detected']} | {v['criterion_hit']} |" for d, v in sorted(s['by_defect'].items())]
    lines += ['', '| Caso | Esperado | Veredito | Notas | Problemas |', '|---|---|---|---|---|']
    for r in report['results']:
        notes = ', '.join(f'{k} {v}' for k, v in (r['scores'] or {}).items()) or '-'
        lines.append(f"| {r['id']} | {r['expect']} | {r['verdict']}{'' if r['correct'] else ' ✗'} | {notes} | {'; '.join(r['issues'] or []) or '-'} |")
    return '\n'.join(lines) + '\n'


async def amain(argv: list[str] | None = None, *, gateway=None) -> int:
    p = argparse.ArgumentParser(prog='python -m evaluation.calibrate_review', description=__doc__.split('\n')[0])
    p.add_argument('--real', action='store_true', help='usa a OpenAI (gasta tokens); exige --max-cases e --max-total-tokens')
    p.add_argument('--max-cases', type=int)
    p.add_argument('--max-total-tokens', type=int)
    p.add_argument('--dataset', type=Path, default=DATASET)
    p.add_argument('--out-dir', type=Path, default=REPORTS)
    args = p.parse_args(argv)
    if args.real and (not args.max_cases or not args.max_total_tokens):
        print('Modo real exige --max-cases e --max-total-tokens (orçamento obrigatório).', file=sys.stderr)
        return 2
    cases = load_cases(args.dataset)[:args.max_cases] if args.max_cases else load_cases(args.dataset)
    model = 'oraculo'
    if args.real and gateway is None:
        from openai import AsyncOpenAI
        from app.adapters.outbound.openai.gateway import OpenAIModelGateway
        from app.bootstrap.settings import get_settings
        settings = get_settings()
        if not settings.openai_api_key:
            print('OPENAI_API_KEY não configurada.', file=sys.stderr)
            return 2
        gateway, model = OpenAIModelGateway(AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())), settings.openai_model
    gateway = gateway or Oracle({c['id']: c for c in cases})
    results, used, worst = [], 0, 0
    for case in cases:
        if args.max_total_tokens and used + max(worst, 1) > args.max_total_tokens:
            print(f'Orçamento de tokens atingido ({used} usados); {len(cases) - len(results)} casos não rodaram.')
            break
        r = score(case, await judge_case(gateway, model, case))
        used, worst = used + r['tokens'], max(worst, r['tokens'])
        results.append(r)
        print(f"[{len(results)}/{len(cases)}] {case['id']}: {r['verdict']} ({'ok' if r['correct'] else 'errado'})")
    started = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    report = {'mode': 'real' if args.real else 'simulated', 'model': model, 'dataset': str(args.dataset.name),
              'sha256': dataset_digest(args.dataset), 'started_at': started, 'results': results, 'summary': summarize(results)}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.out_dir / f'review-{started}'
    stem.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    stem.with_suffix('.md').write_text(render(report), encoding='utf-8')
    print(f"Relatório: {stem}.md; recall {report['summary']['recall_revisar']}, especificidade {report['summary']['specificity']}")
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == '__main__':
    main()
