"""The ten PDF scenarios and the negative cases, end to end: POST /api/v1/chat on the real orchestrator, tools, evidence ledger
and Mongo (throw-away database), with a scripted model standing in for the provider. Skipped when Mongo is down.

Each case asserts the dataset expectations with its own checks on the raw API data (events, evidence, run), not through the
evaluation runner's scoring, so a mistake in the heuristics cannot hide a mistake in the system.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `evaluation/` sits beside `src/`, which pytest's pythonpath does not cover

import pytest  # noqa: E402
from app.domain.evidence import cited_ids  # noqa: E402
from evaluation import metrics  # noqa: E402
from evaluation.run_eval import parse_sse  # noqa: E402
from evaluation.simulated import mongo_available, simulated_app  # noqa: E402

pytestmark = pytest.mark.skipif(not mongo_available(), reason='Mongo local indisponível')
CASES = metrics.load_dataset()
RUN_CASES = [c for c in CASES if 'http_status' not in c['expect']]
GRAPH_EDGES = {('router', 'support'), ('router', 'knowledge'), ('support', 'knowledge'), ('router', 'escalation')}  # the seeded published graph


@pytest.fixture
async def sim():
    async with simulated_app() as simulation:
        yield simulation


async def chat(sim, message, user_id='cliente1988'):
    return await sim.http.post('/api/v1/chat', json={'message': message, 'user_id': user_id})


async def raw(sim, run_id):
    events = parse_sse((await sim.http.get(f'/api/v1/runs/{run_id}/events')).text)
    evidence = (await sim.http.get(f'/api/v1/runs/{run_id}/evidence')).json()
    return events, evidence, (await sim.http.get(f'/api/v1/runs/{run_id}')).json()


@pytest.mark.parametrize('case', RUN_CASES, ids=lambda c: c['id'])
async def test_case_routes_calls_tools_and_grounds_the_answer(sim, case):
    expect, message = case['expect'], case['message']
    sim.use(case['id'])
    response = await chat(sim, message, case['user_id'])
    body = response.json()
    assert response.status_code == 200 and body['status'] == 'completed', body
    assert not sim.gateway.errors and not sim.gateway.pending()  # the scripted path ran from start to end
    events, evidence, run = await raw(sim, body['run_id'])
    answer = body['answer']

    # routing: exactly the expected agents, each started once, only along edges of the published graph
    started = [e['payload']['agent_id'] for e in events if e['type'] == 'agent.started']
    assert sorted(started) == sorted(expect['agents'])
    assert {(e['payload']['agent_id'], e['payload']['destination']) for e in events if e['type'] == 'route.selected'} <= GRAPH_EDGES
    # tools: the expected ones, no refusal, no failure
    tools = [e['payload']['tool'] for e in events if e['type'] == 'tool.started' and e['payload']['tool'] != 'delegate_to_agent']
    assert sorted(tools) == sorted(expect['tools'])
    assert not [e for e in events if e['type'] in ('tool.failed', 'agent.failed')]
    assert events[-1]['type'] == 'run.completed'

    # evidence: cited is a subset of retrieved, the response sources are exactly the cited ones, no invalid citation
    retrieved = {i['evidence_id']: i for i in evidence['retrieved']}
    cited = evidence['cited']
    assert evidence['invalid_citations'] == [] and {i['evidence_id'] for i in cited} <= set(retrieved)
    assert set(cited_ids(answer)) == {i['evidence_id'] for i in cited}  # every [n] in the text is a real, retrieved item
    assert [s['evidence_id'] for s in body['sources']] == [i['evidence_id'] for i in cited]
    if not {'search_knowledge', 'web_search'} & set(expect['tools']):  # fixture data and refusals carry no citation
        assert cited == [] and body['sources'] == [] and not re.search(r'\[\d', answer)
    if expect['cites_any']:
        assert {i['source_id'] for i in cited} & set(expect['cites_any'])
        for item in cited:  # URL and collection date travel with each cited source, and match the curated catalog
            assert item['url'] == sim.corpus[item['source_id']]['source_url'] and item['retrieved_on'] == '2026-11-03' and item['kind'] == 'curated'
    if expect['web']:
        web = [i for i in cited if i['kind'] == 'web']
        assert web and web[0]['url'].startswith('https://') and web[0]['retrieved_on'] in answer and 'web' in answer.lower()

    # no invented data
    if expect.get('facts_from_tool'):
        sale = await sim.facts()
        assert 'R$ 3.482,90' in answer and sale['due_date'].strftime('%d/%m/%Y') in answer
        assert set(re.findall(r'R\$\s*\d[\d.]*,\d{2}', answer)) == {'R$ 3.482,90'}
        assert set(re.findall(r'\d{2}/\d{2}/\d{4}', answer)) == {sale['sale_date'].strftime('%d/%m/%Y'), sale['due_date'].strftime('%d/%m/%Y')}
    if 'get_terminal_status' in expect['tools']:
        terminal = await sim.db.terminals.find_one({'workspace_id': 'demo', 'user_id': 'cliente1988'})
        assert terminal['error'] in answer and terminal['model'] in answer
    if expect.get('mentions_demo'):
        assert metrics.mentions_demo(answer)
    if expect['limitation']:
        assert metrics.has_limitation(answer)
    if expect.get('forbid_numbers'):
        assert metrics.number_claims(answer) == []
    if expect.get('refuses'):
        assert metrics.refuses(answer) and tools == [] and cited == []

    # accounting: tokens are recorded on the run, and an unknown model price stays unknown instead of becoming zero
    assert run['usage']['total_tokens'] > 0 and run['usage']['cost_usd'] > 0 and run['status'] == 'completed' and run['trace_id']


async def test_yesterdays_sale_comes_from_the_fixture_and_is_dated_relative_to_today(sim):
    sim.use('pdf-03-yesterday-deposit')
    facts = await sim.facts()
    brt_today = datetime.now(timezone(timedelta(hours=-3))).date()  # the seed anchors the fixture on Brazil time
    assert facts['sale_date'] == brt_today - timedelta(days=1) and facts['due_date'] > brt_today


async def test_retrieved_passages_that_were_not_used_are_recorded_but_not_cited(sim):
    sim.use('pdf-04-pix-bank-account')
    body = (await chat(sim, 'Do I need a bank account to receive my sales via Pix?')).json()
    _, evidence, _ = await raw(sim, body['run_id'])
    assert [i['source_id'] for i in evidence['retrieved']] == ['04-pix-comercial', '05-pix-get-smart', '09-status-vendas']
    assert [i['source_id'] for i in evidence['cited']] == ['04-pix-comercial', '05-pix-get-smart']
    assert [s['evidence_id'] for s in body['sources']] == [1, 2]
    assert all('text' not in s for s in body['sources'])  # the passage text is not exposed as a source


async def test_pix_answer_does_not_promise_one_rule_and_carries_both_scopes(sim):
    sim.use('pdf-04-pix-bank-account')
    answer = (await chat(sim, 'Do I need a bank account to receive my sales via Pix?')).json()['answer']
    assert 'depends' in answer and 'single rule' in answer and answer.count('[1]') == 1 and answer.count('[2]') == 1  # each modality with its own citation


async def test_terminal_case_shows_cooperation_support_then_knowledge_with_the_diagnosis_in_the_task(sim):
    sim.use('pdf-05-machine-offline')
    body = (await chat(sim, 'My card machine won\'t connect to the internet, what should I do?')).json()
    events, _, _ = await raw(sim, body['run_id'])
    assert [(e['payload']['agent_id'], e['payload']['destination']) for e in events if e['type'] == 'route.selected'] == [('router', 'support'), ('support', 'knowledge')]
    order = [e['payload']['tool'] for e in events if e['type'] == 'tool.started']
    assert order == ['delegate_to_agent', 'get_terminal_status', 'delegate_to_agent', 'search_knowledge']
    handed_over = [m for m in sim.gateway.transcript if 'Algar chip' in m and 'Sem sinal da rede móvel' in m]
    assert handed_over  # the specialist was given the diagnosis the support agent got from the tool


async def test_unknown_user_is_forbidden_before_any_run_or_model_call(sim):
    case = next(c for c in CASES if c['id'] == 'neg-05-unknown-user')
    sim.use(case['id'])
    response = await chat(sim, case['message'], case['user_id'])
    assert response.status_code == case['expect']['http_status'] and response.json()['code'] == 'forbidden' and response.json()['request_id']
    assert sim.gateway.spoke == [] and sim.knowledge.queries == []
    assert await sim.db.runs.count_documents({}) == 0 and await sim.db.conversations.count_documents({}) == 0


async def test_a_run_never_leaks_the_scripted_prompt_or_the_simulated_key(sim):
    sim.use('neg-02-prompt-injection')
    body = (await chat(sim, next(c['message'] for c in CASES if c['id'] == 'neg-02-prompt-injection'))).json()
    events, evidence, run = await raw(sim, body['run_id'])
    everything = json.dumps([body, events, evidence, run], ensure_ascii=False)
    assert metrics.leaks(everything) == [] and 'simulated-key' not in everything
