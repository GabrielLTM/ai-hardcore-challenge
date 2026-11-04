"""Load generator (asyncio + httpx): N concurrent clients, each looping create conversation -> start run -> follow its SSE.

    python loadtest/load.py --base-url http://127.0.0.1:8001 --clients 10 --runs-per-client 5 [--json out.json]

Latencies are measured by the client from the moment the run is requested (POST), so they include the API, Mongo
event persistence and the SSE polling interval, plus whatever model latency the server simulates. A run rejected with
429 counts as an attempt (the client pauses `--retry-pause` seconds, it does not retry), so attempts = clients x runs.
"""
import argparse
import asyncio
import json
import math
import time
from uuid import uuid4
import httpx


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; with few samples p95/p99 are simply the largest values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def summarize(values: list[float]) -> dict:
    return {'n': len(values), **{f'p{q}': round(percentile(values, q), 3) for q in (50, 95, 99)}, 'max': round(max(values), 3)} if values else {'n': 0}


async def one_attempt(http: httpx.AsyncClient, user_id: str) -> dict:
    record = {'outcome': 'error', 'events': 0}
    try:
        conversation = await http.post('/api/v1/conversations', json={'title': 'carga'})
        conversation.raise_for_status()
        started = time.perf_counter()
        response = await http.post(f"/api/v1/conversations/{conversation.json()['id']}/runs", headers={'Idempotency-Key': uuid4().hex},
                                   json={'message': 'Qual o prazo de recebimento do Pix?', 'user_id': user_id})
        record['post_s'] = time.perf_counter() - started
        if response.status_code == 429:
            return {**record, 'outcome': 'rejected_429'}
        if response.status_code != 202:
            return {**record, 'outcome': f'http_{response.status_code}'}
        event, first_delta = None, None
        async with http.stream('GET', response.json()['events_url']) as stream:
            if stream.status_code != 200:
                return {**record, 'outcome': f'sse_http_{stream.status_code}'}
            async for line in stream.aiter_lines():
                if line.startswith('event: '):
                    event = line[7:]
                elif line.startswith('data: '):
                    now = time.perf_counter() - started
                    record['events'] += 1
                    record.setdefault('first_event_s', now)
                    if event == 'response.delta' and first_delta is None:
                        first_delta = record['first_delta_s'] = now
                    if event in ('run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'):
                        record.update(done_s=now, outcome=event.removeprefix('run.'))
    except httpx.HTTPError as exc:
        record['outcome'] = f'client_{type(exc).__name__}'
    return record


async def run_scenario(base_url: str, clients: int, runs_per_client: int, user_id: str = 'cliente1988', retry_pause: float = 1.0) -> dict:
    records: list[dict] = []
    limits = httpx.Limits(max_connections=clients * 2 + 10, max_keepalive_connections=clients * 2 + 10)
    async with httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(120.0), limits=limits) as http:
        async def client():
            for _ in range(runs_per_client):
                records.append(record := await one_attempt(http, user_id))
                if record['outcome'] == 'rejected_429':
                    await asyncio.sleep(retry_pause)
        began, cpu_began = time.perf_counter(), time.process_time()
        await asyncio.gather(*(client() for _ in range(clients)))
        wall, client_cpu = time.perf_counter() - began, time.process_time() - cpu_began
    outcomes: dict[str, int] = {}
    for record in records:
        outcomes[record['outcome']] = outcomes.get(record['outcome'], 0) + 1
    completed = [r for r in records if r['outcome'] == 'completed']
    events = sum(r['events'] for r in records)
    return {'clients': clients, 'runs_per_client': runs_per_client, 'attempts': len(records), 'outcomes': outcomes,
            'error_rate': round(sum(n for k, n in outcomes.items() if k not in ('completed', 'rejected_429')) / len(records), 4),
            'rejected_429_rate': round(outcomes.get('rejected_429', 0) / len(records), 4),
            'wall_s': round(wall, 2), 'load_generator_cpu_pct': round(client_cpu / wall * 100, 1),  # share of one core: near 100 means the client is the limit
            'completed_per_s': round(len(completed) / wall, 2), 'events_total': events,
            'events_per_s': round(events / wall, 1), 'events_per_run': round(events / max(1, len(completed)), 1),
            'stream_seconds': round(sum(r.get('done_s', 0) for r in completed), 1),  # sum of time each run spent streaming
            'latency_s': {name: summarize([r[key] for r in completed if key in r]) for name, key in
                          (('post_202', 'post_s'), ('first_event', 'first_event_s'), ('first_delta', 'first_delta_s'), ('done', 'done_s'))}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', default='http://127.0.0.1:8001')
    parser.add_argument('--clients', type=int, default=10)
    parser.add_argument('--runs-per-client', type=int, default=5)
    parser.add_argument('--retry-pause', type=float, default=1.0)
    parser.add_argument('--json', help='Escreve o resultado neste arquivo.')
    args = parser.parse_args()
    result = asyncio.run(run_scenario(args.base_url, args.clients, args.runs_per_client, retry_pause=args.retry_pause))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.json:
        with open(args.json, 'w') as out:
            json.dump(result, out, indent=2, ensure_ascii=False)
