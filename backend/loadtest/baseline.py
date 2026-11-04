"""Capacity baseline: starts local API instances on the Compose Mongo with MODEL_PROVIDER=fake, drives load.py against
them (1/10/50 clients, plus exploratory scenarios) and finally kills an instance in the middle of active runs.

    cd backend && .venv/bin/python loadtest/baseline.py [--skip-failover] [--out loadtest/results/baseline.json]

The model is simulated (no OpenAI call, no cost) and the database is a throw-away `getnet_loadtest_*` one that is dropped
at the end; `docker compose down` and volumes are never touched. Results are test scenarios, not guaranteed capacity.
"""
import argparse
import asyncio
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path
import httpx
from pymongo import MongoClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BACKEND / 'src'), str(Path(__file__).parent)]
from load import run_scenario  # noqa: E402

ROOT = BACKEND.parent
MONGO_URI = 'mongodb://localhost:27017/?directConnection=true'
OPS = ('insert', 'query', 'update', 'getmore', 'command')


def sh(*cmd: str, cwd: Path | None = None) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=cwd).stdout.strip()
    except Exception:
        return ''


def parse_cpu(text: str) -> float:
    """`ps -o time=` -> seconds ([[dd-]hh:]mm:ss[.cc])."""
    days, _, clock = text.strip().rpartition('-')
    seconds = 0.0
    for part in clock.split(':'):
        seconds = seconds * 60 + float(part)
    return seconds + (float(days) * 86400 if days else 0)


class Instance:
    """One uvicorn process (single worker, like the container image) running the API."""

    def __init__(self, port: int, db: str, **env: str):
        self.port, self.url = port, f'http://127.0.0.1:{port}'
        self.env = {**os.environ, 'PYTHONPATH': 'src', 'APP_PROFILE': 'local-demo', 'MODEL_PROVIDER': 'fake',
                    'OPENAI_API_KEY': 'fake-not-used',  # routes still require a non-empty key; the fake model never uses it
                    'MONGO_URI': MONGO_URI, 'MONGO_DATABASE': db, **env}
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        log = open(BACKEND / 'loadtest' / 'results' / f'instance-{self.port}.log', 'ab')
        self.proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.adapters.inbound.http.main:app', '--host', '127.0.0.1',
                                      '--port', str(self.port), '--log-level', 'warning'], cwd=BACKEND, env=self.env, stdout=log, stderr=log)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and self.proc.poll() is None:
            try:
                if httpx.get(f'{self.url}/api/v1/health/ready', timeout=2).status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.5)
        self.stop()
        raise RuntimeError(f'a instância na porta {self.port} não ficou pronta (veja loadtest/results/instance-{self.port}.log)')

    def __exit__(self, *_):
        self.stop()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def kill(self):  # SIGKILL: no shutdown hook runs, runs keep their lease until it expires
        self.proc.kill()
        self.proc.wait()

    def cpu_seconds(self) -> float:
        return parse_cpu(sh('ps', '-o', 'time=', '-p', str(self.proc.pid)) or '0')

    def rss_mb(self) -> float:
        return int(sh('ps', '-o', 'rss=', '-p', str(self.proc.pid)) or 0) / 1024


class Mongo:
    def __init__(self):
        self.client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        self.container = sh('docker', 'compose', 'ps', '-q', 'mongo', cwd=ROOT)

    def ops(self) -> dict[str, int]:
        counters = self.client.admin.command('serverStatus')['opcounters']
        return {k: counters[k] for k in OPS}

    async def container_stats(self) -> tuple[float, float] | None:
        """(CPU % of one core, memory MiB) of the Mongo container; None when Docker is not available."""
        if not self.container:
            return None
        proc = await asyncio.create_subprocess_exec('docker', 'stats', '--no-stream', '--format', '{{.CPUPerc}}|{{.MemUsage}}', self.container,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out = (await proc.communicate())[0].decode().strip()
        try:
            cpu, memory = out.split('|')
            amount, unit = re.fullmatch(r'([\d.]+)([KMG]?i?B)', memory.split('/')[0].strip()).groups()
            return float(cpu.rstrip('%')), float(amount) * {'B': 1 / 2**20, 'KiB': 1 / 1024, 'MiB': 1, 'GiB': 1024}[unit]
        except (ValueError, AttributeError, KeyError):
            return None


async def sample(instance: Instance, mongo: Mongo, stop: asyncio.Event, out: dict):
    """Background sampling while a scenario runs (every ~2 s: `docker stats` itself takes that long)."""
    rss, cpu, memory = [], [], []
    while not stop.is_set():
        rss.append(instance.rss_mb())
        stats = await mongo.container_stats()
        if stats:
            cpu.append(stats[0]); memory.append(stats[1])
    out.update(api_rss_mb_max=round(max(rss, default=0), 1),
               mongo_cpu_pct_avg=round(sum(cpu) / len(cpu), 1) if cpu else None, mongo_cpu_pct_max=round(max(cpu), 1) if cpu else None,
               mongo_mem_mib_max=round(max(memory), 1) if memory else None)


async def measure(instance: Instance, mongo: Mongo, clients: int, runs_per_client: int) -> dict:
    ops_before, cpu_before, stop, extra = mongo.ops(), instance.cpu_seconds(), asyncio.Event(), {}
    sampler = asyncio.create_task(sample(instance, mongo, stop, extra))
    result = await run_scenario(instance.url, clients, runs_per_client)
    stop.set()
    await sampler
    ops_after, wall = mongo.ops(), result['wall_s']
    delta = {k: ops_after[k] - ops_before[k] for k in OPS}
    completed = max(1, result['outcomes'].get('completed', 0))
    result['api_cpu_pct_avg'] = round((instance.cpu_seconds() - cpu_before) / wall * 100, 1)  # share of one core
    result['mongo_ops_per_s'] = {k: round(v / wall, 1) for k, v in delta.items()}
    result['mongo_ops_per_completed_run'] = {k: round(v / completed, 1) for k, v in delta.items()}
    result['mongo_reads_per_stream_second'] = round((delta['query'] + delta['getmore']) / max(1.0, result['stream_seconds']), 2)
    return {**result, **extra}


# --- failure of an instance in the middle of active runs ----------------------------------------------------------

async def consume(base_url: str, run_id: str, after: int, sink: list[dict]) -> str:
    """Follow a run's SSE appending (seq, type, text); returns how the stream ended: terminal event type, 'cut' or 'closed'."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as http, http.stream('GET', f'{base_url}/api/v1/runs/{run_id}/events', params={'after': after}) as stream:
            async for line in stream.aiter_lines():
                if line.startswith('data: '):
                    event = json.loads(line[6:])
                    sink.append({'seq': event['seq'], 'type': event['type'], 'text': event['payload'].get('text', ''), 'payload': event['payload']})
                    if event['type'].startswith('run.') and event['type'] != 'run.started':
                        return event['type']
    except httpx.HTTPError:
        return 'cut'
    return 'closed'


async def failover(db: str, runs: int = 8) -> dict:
    with Instance(8001, db, FAKE_MODEL_DELTA_MS='300') as victim, Instance(8002, db) as survivor:  # the victim streams slowly: runs outlive the kill
        async with httpx.AsyncClient(timeout=30.0) as http:
            started = []
            for _ in range(runs):
                conversation = (await http.post(f'{victim.url}/api/v1/conversations', json={'title': 'falha'})).json()['id']
                response = await http.post(f'{victim.url}/api/v1/conversations/{conversation}/runs', headers={'Idempotency-Key': f'f-{time.time_ns()}'},
                                           json={'message': 'Qual o prazo de recebimento do Pix?', 'user_id': 'cliente1988'})
                started.append({'conversation': conversation, 'run': response.json()['run_id'], 'events': []})
            watchers = [asyncio.create_task(consume(victim.url, r['run'], 0, r['events'])) for r in started]
            partial_before, deadline = {}, time.monotonic() + 90  # read the state through the *other* instance
            while len(partial_before) < runs and time.monotonic() < deadline:
                await asyncio.sleep(0.5)
                for r in started:
                    view = (await http.get(f"{survivor.url}/api/v1/runs/{r['run']}")).json()
                    if len(view['partial_text']) >= 100 and r['run'] not in partial_before:  # a partial worth preserving
                        partial_before[r['run']] = view['partial_text']
            assert len(partial_before) == runs, 'as execuções não começaram a responder a tempo'
            victim.kill()
            killed = time.monotonic()
            cuts = await asyncio.gather(*watchers)
            just_after = [(await http.get(f"{survivor.url}/api/v1/runs/{r['run']}")).json()['status'] for r in started]
            endings = await asyncio.gather(*(consume(survivor.url, r['run'], r['events'][-1]['seq'], r['events']) for r in started))
            seen_after_s = round(time.monotonic() - killed, 1)  # every client has been told the final state by now
            checks = []
            for r in started:
                before = partial_before[r['run']]
                view = (await http.get(f"{survivor.url}/api/v1/runs/{r['run']}")).json()
                seqs = [e['seq'] for e in r['events']]
                deltas = ''.join(e['text'] for e in r['events'] if e['type'] == 'response.delta')
                checks.append({'status': view['status'], 'error_code': (view['error'] or {}).get('code'),
                               'partial_chars_before_kill': len(before), 'partial_chars_after': len(view['partial_text']),
                               'partial_preserved': view['partial_text'].startswith(before), 'seq_contiguous_no_duplicates': seqs == list(range(1, len(seqs) + 1)),
                               'sse_text_equals_partial': deltas == view['partial_text'], 'terminal_event': r['events'][-1]['type'],
                               'terminal_code': r['events'][-1]['payload'].get('code')})
            retry = await http.post(f"{survivor.url}/api/v1/conversations/{started[0]['conversation']}/runs", headers={'Idempotency-Key': f'again-{time.time_ns()}'},
                                    json={'message': 'Qual o prazo de recebimento do Pix?', 'user_id': 'cliente1988'})
            retried = []
            if retry.status_code == 202:
                await consume(survivor.url, retry.json()['run_id'], 0, retried)
        return {'runs': runs, 'victim_streams_cut_by_kill': cuts.count('cut') + cuts.count('closed'), 'status_just_after_kill': sorted(set(just_after)),
                'clients_told_final_state_after_s': seen_after_s, 'stream_endings_on_survivor': sorted(set(endings)), 'per_run': checks,
                'new_run_in_same_conversation': {'http': retry.status_code, 'terminal': retried[-1]['type'] if retried else None}}


# --- main ---------------------------------------------------------------------------------------------------------

def machine() -> dict:
    docker = sh('docker', 'info', '--format', '{{.NCPU}} CPUs|{{.MemTotal}} bytes|Docker {{.ServerVersion}}')
    return {'os': platform.platform(), 'cpu': sh('sysctl', '-n', 'machdep.cpu.brand_string') or platform.processor(),
            'cores': os.cpu_count(), 'ram_gib': round(int(sh('sysctl', '-n', 'hw.memsize') or 0) / 2**30, 1), 'python': platform.python_version(),
            'docker_vm': docker, 'mongo_image': sh('docker', 'inspect', '-f', '{{.Config.Image}}', sh('docker', 'compose', 'ps', '-q', 'mongo', cwd=ROOT)),
            'packages': {p: version(p) for p in ('fastapi', 'uvicorn', 'pymongo', 'httpx', 'uvloop')}}


async def main(args):
    from app.adapters.inbound.http.run_settings import RunSettings
    from app.adapters.outbound.fake.model import FakeModelSettings
    db = f"getnet_loadtest_{time.strftime('%Y%m%d%H%M%S', time.gmtime())}"
    mongo, only = Mongo(), set(args.only.split(',')) if args.only else None
    results = {'when_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'machine': machine(), 'database': db,
               'fake_model': FakeModelSettings(_env_file=None).model_dump(), 'run_defaults': RunSettings(_env_file=None).model_dump(),
               'scenarios': {}}
    plan = [('cap20', {}, [(1, 15), (10, 10), (50, 6)]),  # default MAX_ACTIVE_RUNS=20 per workspace
            ('cap200', {'MAX_ACTIVE_RUNS': '200'}, [(50, 6), (100, 4)]),  # cap out of the way: what the process itself sustains
            ('cap200_poll2s', {'MAX_ACTIVE_RUNS': '200', 'SSE_POLL_SECONDS': '2'}, [(50, 6)])]  # same load, SSE polls 4x less often
    try:
        for label, env, scenarios in plan:
            if only and label not in only:
                continue
            with Instance(8001, db, **env) as instance:
                await run_scenario(instance.url, 1, 2)  # warm-up (imports, pools, seed), discarded
                for clients, runs in scenarios:
                    name = f'{label}/{clients}_clients'
                    print(f'>> {name}', flush=True)
                    results['scenarios'][name] = {'env': env, **await measure(instance, mongo, clients, runs)}
        if not args.skip_failover and (not only or 'failover' in only):
            print('>> failover', flush=True)
            results['failover'] = await failover(db)
    finally:
        mongo.client.drop_database(db)
    Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--skip-failover', action='store_true')
    parser.add_argument('--only', help='Lista separada por vírgula entre cap20, cap200, cap200_poll2s e failover.')
    parser.add_argument('--out', default=str(BACKEND / 'loadtest' / 'results' / 'baseline.json'))
    asyncio.run(main(parser.parse_args()))
