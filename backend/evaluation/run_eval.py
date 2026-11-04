"""Runs the evaluation dataset through POST /api/v1/chat and writes reports/<timestamp>.json and .md.

Simulated (default, free, for CI): an in-process app with a scripted model; needs the local Mongo.
    python -m evaluation.run_eval
Real (spends tokens, needs a running API with OPENAI_API_KEY configured on the server): budget flags are mandatory.
    python -m evaluation.run_eval --real --max-cases 3 --max-total-tokens 40000 [--base-url http://localhost:8000]
The key never passes through this program: the server holds it.
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import httpx
from evaluation.metrics import DEFAULT_DATASET, DELEGATE, aggregate, dataset_digest, load_dataset, score_case
from evaluation.report import REPORTS, write_report

REPO = Path(__file__).resolve().parents[2]
TERMINAL = ('completed', 'failed', 'cancelled', 'interrupted')
EVIDENCE_FIELDS = ('evidence_id', 'kind', 'source_id', 'title', 'url', 'retrieved_on')
# shortcut: the real database cannot be read through the API, so the amount is the seed constant (seed.py) and the due date
# is only checked by shape; the exact date is verified in the simulated suite. Update when the seed changes.
SEED_AMOUNT = Decimal('3482.90')


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _slim(items: list[dict]) -> list[dict]:
    return [{k: i.get(k) for k in EVIDENCE_FIELDS} for i in items]  # passage text stays out of reports


def parse_sse(text: str) -> list[dict]:
    frames = [[line[6:] for line in block.split('\n') if line.startswith('data: ')] for block in text.split('\n\n')]
    return [json.loads(f[0]) for f in frames if f]


def source_urls(root: Path = REPO / 'data' / 'knowledge' / 'getnet') -> dict[str, str]:
    """Official URL of each curated source: citing the live-ingested page of the same URL also counts."""
    manifest = root / 'manifest.json'
    return {d['id']: d['source_url'] for d in json.loads(manifest.read_text(encoding='utf-8'))['documents']} if manifest.exists() else {}


async def observe(client: httpx.AsyncClient, case: dict, *, timeout: float = 120.0, poll: float = 0.5) -> dict:
    """Send the case and collect what the API recorded: answer, agents, tools, evidence, usage, latency."""
    started = time.perf_counter()
    try:
        response = await client.post('/api/v1/chat', json={'message': case['message'], 'user_id': case['user_id']})
    except httpx.HTTPError as exc:
        return {'error': f'API inacessível ({type(exc).__name__})', 'fatal': True}
    body = _json(response)
    obs: dict = {'status_code': response.status_code, 'answer': body.get('answer') or ''}
    if response.status_code == 503:  # provider not configured or API not ready: nothing else can work
        return {**obs, 'error': body.get('message') or 'API indisponível (503)', 'fatal': True}
    run_id = body.get('run_id') or (body.get('details') or {}).get('run_id')
    timed_out = False
    if response.status_code == 202 and run_id:
        deadline = started + timeout
        while time.perf_counter() < deadline:
            if _json(await client.get(f'/api/v1/runs/{run_id}')).get('status') in TERMINAL:
                break
            await asyncio.sleep(poll)
        else:
            timed_out = True
            await client.post(f'/api/v1/runs/{run_id}/cancel')  # stop spending on a stuck run
            obs['error'] = 'tempo limite do caso; execução cancelada'
    obs['latency_ms'] = (time.perf_counter() - started) * 1000
    if not run_id:
        return {**obs, 'error': obs.get('error') or body.get('message') or f'HTTP {response.status_code}'}
    run = _json(await client.get(f'/api/v1/runs/{run_id}'))
    obs |= {'run_id': run_id, 'run_status': run.get('status'), 'usage': run.get('usage'), 'answer': run.get('partial_text') or obs['answer']}
    if run.get('error') and not obs.get('error'):
        obs['error'] = run['error'].get('message')
    if timed_out:
        return obs
    events = parse_sse((await client.get(f'/api/v1/runs/{run_id}/events')).text)
    evidence = _json(await client.get(f'/api/v1/runs/{run_id}/evidence'))
    return obs | {
        'agents': list(dict.fromkeys(e['payload']['agent_id'] for e in events if e['type'] == 'agent.started')),
        'tools': [e['payload']['tool'] for e in events if e['type'] == 'tool.started' and e['payload']['tool'] != DELEGATE],
        'tool_failures': [{'tool': e['payload']['tool'], 'code': e['payload']['code']} for e in events if e['type'] == 'tool.failed'],
        'routes': [(e['payload']['agent_id'], e['payload']['destination']) for e in events if e['type'] == 'route.selected'],
        'retrieved': _slim(evidence.get('retrieved', [])), 'cited': _slim(evidence.get('cited', [])),
        'invalid_citations': evidence.get('invalid_citations', [])}


async def run_dataset(cases: list[dict], observe_case, *, max_cases: int | None = None, max_total_tokens: int | None = None,
                      facts: dict | None = None, urls: dict[str, str] | None = None, on_result=None) -> dict:
    """Run cases in order under the budget. A case is not started when the biggest case so far would exceed the token cap.

    shortcut: the first case has no estimate, so a very low cap can still be crossed once; cap the server too (RUN_MAX_OUTPUT_TOKENS)."""
    selected = cases[:max_cases] if max_cases else cases
    results, used, worst, stop = [], 0, 0, None
    for index, case in enumerate(selected):
        if stop is None and max_total_tokens is not None and used + max(worst, 1) > max_total_tokens:
            stop = {'by': 'budget', 'reason': f'orçamento de tokens ({max_total_tokens}) seria excedido: {used} usados, maior caso até agora {worst}'}
        if stop:
            results.append({'id': case['id'], 'status': 'skipped', 'passed': None, 'reason': stop['reason']})
            continue
        obs = await observe_case(case)
        result = score_case(case, obs, facts=facts, source_urls=urls)
        spent = (obs.get('usage') or {}).get('total_tokens') or 0
        used, worst = used + spent, max(worst, spent)
        results.append(result)
        if on_result:
            on_result(index + 1, len(selected), result)
        if obs.get('fatal'):
            stop = {'by': 'error', 'reason': obs['error']}
    return {'cases': results, 'budget': {'max_cases': max_cases, 'max_total_tokens': max_total_tokens, 'tokens_used': used,
                                        'aborted': stop and stop['reason'], 'aborted_by': stop and stop['by']}}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='python -m evaluation.run_eval', description=__doc__.split('\n')[0])
    p.add_argument('--real', action='store_true', help='chama a API em --base-url (gasta tokens); exige --max-cases e --max-total-tokens')
    p.add_argument('--base-url', help='só com --real; padrão http://localhost:8000')
    p.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    p.add_argument('--max-cases', type=int, help='executa só os N primeiros casos')
    p.add_argument('--max-total-tokens', type=int, help='teto de tokens da rodada; não inicia caso que o excederia')
    p.add_argument('--only', help='ids de casos separados por vírgula')
    p.add_argument('--out-dir', type=Path, default=REPORTS)
    p.add_argument('--timeout', type=float, default=120.0, help='segundos por caso antes de cancelar a execução')
    return p


def _fail(message: str) -> int:
    print(f'erro: {message}', file=sys.stderr)
    return 2


def _display(path: Path) -> str:
    resolved = path.resolve()
    return str(resolved.relative_to(REPO)) if resolved.is_relative_to(REPO) else str(resolved)


def _progress(i: int, n: int, result: dict) -> None:
    print(f"[{i}/{n}] {result['id']}: {'aprovado' if result['passed'] else 'reprovado ' + ','.join(result['failed_checks']) + ' ' + str(result.get('error') or '')}", flush=True)


async def amain(argv: list[str] | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> int:
    """Exit codes: 0 all executed cases passed, 1 some failed, 2 usage/environment error, 3 stopped by the token budget."""
    args = _parser().parse_args(argv)
    if args.base_url and not args.real:
        return _fail('--base-url só vale com --real (sem --real o app é simulado, em processo)')
    if args.real and (args.max_cases is None or args.max_total_tokens is None):
        return _fail('--real exige --max-cases e --max-total-tokens (orçamento obrigatório)')
    if any(v is not None and v < 1 for v in (args.max_cases, args.max_total_tokens)):
        return _fail('os limites de orçamento devem ser maiores que zero')
    cases = all_cases = load_dataset(args.dataset)
    if args.only:
        wanted = [i.strip() for i in args.only.split(',')]
        if unknown := sorted(set(wanted) - {c['id'] for c in cases}):
            return _fail(f'casos desconhecidos: {", ".join(unknown)}')
        cases = [c for c in cases if c['id'] in wanted]
    started = datetime.now(timezone.utc)
    options = dict(max_cases=args.max_cases, max_total_tokens=args.max_total_tokens, urls=source_urls(), on_result=_progress)
    if args.real:
        base_url = args.base_url or 'http://localhost:8000'
        print(f'Rodada REAL contra {base_url}: gasta tokens do provedor (teto {args.max_total_tokens}, até {args.max_cases} casos).', flush=True)
        async with httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(args.timeout + 30), transport=transport) as client:
            try:
                ready = await client.get('/api/v1/health/ready')
            except httpx.HTTPError as exc:
                return _fail(f'API inacessível em {base_url} ({type(exc).__name__})')
            if ready.status_code != 200:
                return _fail(f'API não está pronta em {base_url} (HTTP {ready.status_code})')
            outcome = await run_dataset(cases, lambda case: observe(client, case, timeout=args.timeout), facts={'amount': SEED_AMOUNT}, **options)
    else:
        from evaluation.simulated import SimulationUnavailable, simulated_app  # imports the app package: not needed for real runs
        try:
            async with simulated_app() as sim:
                async def observe_case(case: dict) -> dict:
                    try:
                        sim.use(case['id'])
                    except LookupError as exc:
                        return {'error': str(exc)}
                    return await observe(sim.http, case, timeout=args.timeout, poll=0.05)
                outcome = await run_dataset(cases, observe_case, facts=await sim.facts(), **options)
        except SimulationUnavailable as exc:
            return _fail(str(exc))
    report = {
        'version': 1, 'mode': 'real' if args.real else 'simulated', 'mode_label': 'rodada real' if args.real else 'simulada, sem chamar a OpenAI',
        'started_at': started.strftime('%Y-%m-%dT%H:%M:%SZ'), 'finished_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'base_url': (args.base_url or 'http://localhost:8000') if args.real else None,
        'dataset': {'path': _display(args.dataset), 'sha256': dataset_digest(args.dataset), 'cases': len(all_cases), 'selected': len(cases)},
        'budget': outcome['budget'], 'cases': outcome['cases'], 'aggregates': aggregate(outcome['cases'])}
    json_path, md_path = write_report(report, args.out_dir)
    agg = report['aggregates']
    print(f"Relatório: {md_path} e {json_path}\nAprovados {agg['passed']}/{agg['cases_executed']}; roteamento {agg['checks']['routing']['rate']}; "
          f"tokens {agg['tokens']['total_tokens']}", flush=True)
    stopped_by = report['budget']['aborted_by']
    return 2 if stopped_by == 'error' else 1 if agg['failed'] else 3 if stopped_by == 'budget' else 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == '__main__':
    main()
