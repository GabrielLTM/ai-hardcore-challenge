"""The evaluation runner end to end in simulated mode (in-process app, scripted model, local Mongo): reports, budget, failure
detection, the real-mode code path against the scripted app, and the stuck-run safeguard. Skipped when Mongo is down."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `evaluation/` sits beside `src/`, which pytest's pythonpath does not cover

import httpx  # noqa: E402
import pytest  # noqa: E402
from app.domain.models import ModelEvent  # noqa: E402
from evaluation import metrics, scenarios  # noqa: E402
from evaluation.run_eval import amain, observe  # noqa: E402
from evaluation.simulated import Scenario, delegate, mongo_available, say, simulated_app  # noqa: E402

pytestmark = pytest.mark.skipif(not mongo_available(), reason='Mongo local indisponível')
CASES = {c['id']: c for c in metrics.load_dataset()}


def load(out_dir: Path) -> dict:
    (path,) = out_dir.glob('*.json')
    assert path.with_suffix('.md').exists()
    return json.loads(path.read_text(encoding='utf-8'))


async def test_simulated_run_passes_every_dataset_case_and_writes_json_and_markdown_reports(tmp_path, capsys):
    assert await amain(['--out-dir', str(tmp_path)]) == 0
    report = load(tmp_path)
    agg = report['aggregates']
    assert report['mode'] == 'simulated' and report['budget']['aborted'] is None and report['dataset']['cases'] == len(CASES)
    assert (agg['cases_executed'], agg['passed'], agg['failed']) == (len(CASES), len(CASES), 0)
    assert agg['checks']['routing'] == {'ok': len(CASES) - 1, 'total': len(CASES) - 1, 'rate': 1.0}  # every case but the 403 one reaches a run
    assert agg['checks']['http_status']['total'] == 1 and agg['citations']['invalid'] == 0 and agg['citations']['valid'] > 0
    assert agg['latency_ms']['p50'] > 0 and agg['latency_ms']['p95'] >= agg['latency_ms']['p50'] and agg['tokens']['total_tokens'] > 0
    assert agg['cost_usd']['known_total'] > 0  # the default model is priced in the versioned table
    assert next(c for c in report['cases'] if c['id'] == 'pdf-05-machine-offline')['agents'] == ['router', 'support', 'knowledge']
    assert '| pdf-03-yesterday-deposit | aprovado |' in next(tmp_path.glob('*.md')).read_text(encoding='utf-8')
    assert 'Aprovados 17/17' in capsys.readouterr().out


async def test_max_cases_and_only_select_what_runs_and_the_token_cap_skips_the_rest(tmp_path):
    assert await amain(['--out-dir', str(tmp_path), '--max-cases', '2']) == 0
    assert load(tmp_path)['aggregates']['cases_executed'] == 2
    other = tmp_path / 'only'
    assert await amain(['--out-dir', str(other), '--only', 'pdf-10-payment-link,neg-04-off-topic']) == 0
    assert [c['id'] for c in load(other)['cases']] == ['pdf-10-payment-link', 'neg-04-off-topic']
    capped = tmp_path / 'capped'
    assert await amain(['--out-dir', str(capped), '--max-total-tokens', '1', '--max-cases', '4']) == 3  # stopped by the budget, nothing failed
    report = load(capped)
    assert [c['status'] for c in report['cases']] == ['passed', 'skipped', 'skipped', 'skipped'] and report['budget']['aborted_by'] == 'budget'
    assert report['aggregates']['cases_skipped'] == 3


async def test_a_regression_in_routing_tools_or_numbers_is_reported_and_fails_the_run(tmp_path, monkeypatch, capsys):
    broken = Scenario(router=[delegate('knowledge', 'Parcelas?'), say(scenarios.relay)], knowledge=[say('Você pode parcelar em até 12 vezes.')])
    monkeypatch.setitem(scenarios.SCENARIOS, 'pdf-09-crediario', broken)  # the model answers from memory, without searching
    assert await amain(['--out-dir', str(tmp_path), '--only', 'pdf-09-crediario,pdf-10-payment-link']) == 1
    report = load(tmp_path)
    assert report['aggregates']['failures'] == [{'id': 'pdf-09-crediario', 'failed_checks': ['tools', 'cites_expected', 'no_numeric_claims'], 'error': None}]
    assert report['aggregates']['pass_rate'] == 0.5
    assert 'reprovado' in capsys.readouterr().out and '## Falhas' in next(tmp_path.glob('*.md')).read_text(encoding='utf-8')


async def test_a_case_without_a_script_is_reported_as_an_error_not_skipped_silently(tmp_path):
    dataset = tmp_path / 'extra.jsonl'
    extra = {'id': 'novo-caso', 'message': 'oi', 'user_id': 'cliente1988', 'expect': {'agents': ['router'], 'tools': []}}
    dataset.write_text(json.dumps(extra) + '\n', encoding='utf-8')
    assert await amain(['--dataset', str(dataset), '--out-dir', str(tmp_path / 'r')]) == 1
    assert 'sem cenário roteirizado' in load(tmp_path / 'r')['cases'][0]['error']


async def test_real_mode_code_path_runs_against_any_http_app_under_the_mandatory_budget(tmp_path):
    async with simulated_app() as sim:  # stands in for a running API: no provider is involved
        sim.use('pdf-10-payment-link')
        argv = ['--real', '--base-url', 'http://api.test', '--max-cases', '1', '--max-total-tokens', '100000', '--only', 'pdf-10-payment-link', '--out-dir', str(tmp_path)]
        assert await amain(argv, transport=httpx.ASGITransport(app=sim.app)) == 0
    report = load(tmp_path)
    assert report['mode'] == 'real' and report['base_url'] == 'http://api.test' and report['budget']['max_total_tokens'] == 100000
    assert report['aggregates']['passed'] == 1 and report['budget']['tokens_used'] > 0


async def test_observation_matches_what_the_api_recorded():
    async with simulated_app() as sim:
        sim.use('pdf-05-machine-offline')
        obs = await observe(sim.http, CASES['pdf-05-machine-offline'], poll=0.02)
        assert (obs['status_code'], obs['run_status']) == (200, 'completed') and obs['latency_ms'] > 0
        assert obs['agents'] == ['router', 'support', 'knowledge'] and sorted(obs['tools']) == ['get_terminal_status', 'search_knowledge']
        assert obs['routes'] == [('router', 'support'), ('support', 'knowledge')] and obs['tool_failures'] == []
        assert [c['source_id'] for c in obs['cited']] == ['08-faq-crediario-conexao'] and obs['invalid_citations'] == []
        assert all('text' not in item for item in obs['retrieved'])  # passage text never lands in reports
        assert obs['usage']['total_tokens'] > 0 and obs['answer'].startswith('Your terminal')


async def test_a_run_that_outlives_the_case_timeout_is_cancelled_so_it_stops_spending():
    class Stuck:
        async def stream(self, **_):
            yield ModelEvent('text_delta', text='Pensando… ')
            await asyncio.Event().wait()

        async def embed(self, *, model, texts): return [[0.1]]

    async with simulated_app(env={'CHAT_WAIT_SECONDS': '0.05'}) as sim:  # POST /chat answers 202 quickly and the run keeps going
        sim.use(Scenario())
        sim.gateway = Stuck()
        obs = await observe(sim.http, CASES['pdf-06-antecipacao'], timeout=0.4, poll=0.05)
        assert obs['error'] == 'tempo limite do caso; execução cancelada' and obs['run_status'] in ('cancelled', 'running')
        result = metrics.score_case(CASES['pdf-06-antecipacao'], obs)
        assert not result['passed'] and 'run_completed' in result['failed_checks']
        run = (await sim.http.get(f"/api/v1/runs/{obs['run_id']}")).json()
        assert run['status'] == 'cancelled' and run['cancel_requested']
