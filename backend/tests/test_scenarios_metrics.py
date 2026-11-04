"""Heuristics, case scoring, aggregates, token budget, report and CLI guards of the evaluation runner. No database needed."""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `evaluation/` sits beside `src/`, which pytest's pythonpath does not cover

import pytest  # noqa: E402
from evaluation import metrics, report, simulated  # noqa: E402
from evaluation.run_eval import amain, run_dataset, source_urls  # noqa: E402
from evaluation.scenarios import SCENARIOS  # noqa: E402

CASES = {c['id']: c for c in metrics.load_dataset()}
URLS = source_urls()


def obs(**fields) -> dict:
    return {'status_code': 200, 'run_status': 'completed', 'answer': '', 'agents': [], 'tools': [], 'tool_failures': [], 'retrieved': [],
            'cited': [], 'invalid_citations': [], 'usage': None, 'latency_ms': 1.0, **fields}


def good(case_id: str, **fields) -> dict:
    expect = CASES[case_id]['expect']
    return obs(agents=expect['agents'], tools=expect['tools'], **fields)


def failed(case_id: str, observation: dict, **kw) -> list[str]:
    return metrics.score_case(CASES[case_id], observation, **kw)['failed_checks']


def test_dataset_keeps_the_ten_pdf_scenarios_and_the_negatives_and_every_case_has_a_script():
    assert [i[:6] for i in CASES if i.startswith('pdf-')] == [f'pdf-{n:02d}' for n in range(1, 11)]
    assert len([i for i in CASES if i.startswith('neg-')]) >= 5
    assert set(SCENARIOS) == set(CASES)
    assert any(c['expect'].get('http_status') == 403 for c in CASES.values())


def test_number_claims_catch_rates_money_installments_and_deadlines_but_not_citations_or_years():
    assert len(metrics.number_claims('Até 12x sem juros, taxa de 2,5% e R$ 10 em 30 dias')) == 4
    assert metrics.number_claims('Parcelas definidas pelo emissor [2] e [12, 3]; em 2027 a regra pode mudar, coleta em 03/11/2026.') == []
    assert metrics.number_claims('Up to 12 installments') != [] and metrics.number_claims('Up to twelve installments') == []  # known blind spot


@pytest.mark.parametrize('text,expected', [('A comparação é parcial.', True), ('Não encontrei essa informação.', True), ('The comparison is partial.', True),
                                           ("I don't have that data.", True), ('Sim, você pode vender pelo WhatsApp.', False)])
def test_limitation_heuristic_in_portuguese_and_english(text, expected):
    assert metrics.has_limitation(text) is expected


def test_demo_refusal_unavailable_and_leak_heuristics():
    assert metrics.mentions_demo('São dados fictícios de demonstração.') and metrics.mentions_demo('These are fictitious demo data.')
    assert not metrics.mentions_demo('Seu recebível é R$ 10,00.')
    assert metrics.refuses('Não posso compartilhar isso.') and metrics.refuses("I can't help with that.") and not metrics.refuses('Claro, segue o poema.')
    assert metrics.states_unavailable('A busca está indisponível.') and not metrics.states_unavailable('Fará sol.')
    assert metrics.leaks('<dados_nao_confiaveis origem="x"> Regras de segurança') == ['dados_nao_confiaveis', 'Regras de segurança']


def test_facts_must_match_the_known_value_and_only_the_shape_when_the_truth_is_unknown():
    facts = {'amount': Decimal('3482.90'), 'due_date': date(2026, 12, 2)}
    assert metrics.facts_present('Valor R$ 3.482,90, crédito em 02/12/2026.', ['amount', 'due_date'], facts)
    assert metrics.facts_present('R$ 3482,90 em 2026-12-02', ['amount', 'due_date'], facts)
    assert not metrics.facts_present('Valor R$ 3.500,00, crédito em 02/12/2026.', ['amount', 'due_date'], facts)
    assert not metrics.facts_present('Valor R$ 3.482,90, crédito em 03/12/2026.', ['amount', 'due_date'], facts)
    assert metrics.facts_present('R$ 99,00 em 05/12/2026', ['amount', 'due_date'], None)  # shape only without a known truth
    assert not metrics.facts_present('sem valor nem data', ['amount', 'due_date'], None)
    assert not metrics.facts_present('R$ 1,00', ['unknown_fact'], None)  # an unknown fact name never passes silently


def test_percentile_is_nearest_rank():
    assert metrics.percentile([40, 10, 30, 20], 50) == 20 and metrics.percentile([40, 10, 30, 20], 95) == 40
    assert metrics.percentile([7], 95) == 7 and metrics.percentile([], 50) is None


def test_a_good_observation_passes_and_a_regression_is_named():
    case = 'pdf-09-crediario'
    answer = 'O número de parcelas é definido pelo emissor do cartão, não há valor fixo [1].'
    cited = [{'evidence_id': 1, 'kind': 'curated', 'source_id': '08-faq-crediario-conexao', 'url': URLS['08-faq-crediario-conexao'], 'retrieved_on': '2026-11-03'}]
    assert failed(case, good(case, answer=answer, cited=cited)) == []
    bad = obs(agents=['router', 'support'], tools=[], answer='Você pode parcelar em até 12 vezes.')
    assert failed(case, bad) == ['routing', 'tools', 'cites_expected', 'no_numeric_claims']


def test_expected_source_may_be_matched_by_its_official_url_when_the_page_was_ingested_from_the_web():
    live = [{'evidence_id': 1, 'kind': 'official', 'source_id': 'official-maquininhas', 'url': URLS['02-maquininhas'], 'retrieved_on': '2026-11-03'}]
    answer = 'A comparação é parcial [1].'
    assert 'cites_expected' not in failed('pdf-01-classica-vs-smart', good('pdf-01-classica-vs-smart', answer=answer, cited=live), source_urls=URLS)
    assert 'cites_expected' in failed('pdf-01-classica-vs-smart', good('pdf-01-classica-vs-smart', answer=answer, cited=live))  # id-only matching


def test_invalid_citation_failed_run_and_prompt_leak_are_failures_of_any_case():
    case = 'pdf-06-antecipacao'
    assert failed(case, good(case, invalid_citations=[7], cited=[{'source_id': '07-antecipacao'}])) == ['citations_valid']
    assert 'run_completed' in failed(case, good(case, run_status='failed', cited=[{'source_id': '07-antecipacao'}]))
    assert failed(case, good(case, cited=[{'source_id': '07-antecipacao'}], answer='<dados_nao_confiaveis origem="x">')) == ['no_prompt_leak']


def test_web_case_needs_a_dated_cited_source_or_an_explicit_unavailability():
    case = 'pdf-02-weather-poa'
    assert failed(case, good(case, answer='Fará sol.')) == ['web']  # asserted without any source
    source = {'evidence_id': 1, 'kind': 'web', 'url': 'https://t.test', 'retrieved_on': '2026-11-03T10:00:00Z'}
    assert failed(case, good(case, answer='Fará sol [1].', cited=[source])) == []
    undated = {**source, 'retrieved_on': None}
    assert failed(case, good(case, answer='Fará sol [1].', cited=[undated])) == ['web']
    outage = good(case, answer='A busca na web está indisponível agora.', tool_failures=[{'tool': 'web_search', 'code': 'unavailable'}])
    assert failed(case, outage) == []  # an honest outage passes; the same outage with an invented answer does not
    assert failed(case, good(case, answer='Fará sol.', tool_failures=[{'tool': 'web_search', 'code': 'unavailable'}])) == ['web']


def test_refusal_cases_fail_when_a_tool_runs_or_the_answer_complies():
    assert failed('neg-04-off-topic', obs(agents=['router'], answer='Não posso ajudar com isso, fora do escopo.')) == []
    assert failed('neg-04-off-topic', obs(agents=['router'], answer='Bola rolando, gol no fim.')) == ['refusal']
    assert failed('neg-02-prompt-injection', obs(agents=['router'], tools=['search_knowledge'], answer='Não posso.')) == ['tools', 'refusal']
    assert failed('neg-01-other-customer', good('neg-01-other-customer', answer='Os recebíveis do cliente 2002 são R$ 10,00.')) == ['limitation', 'no_other_customer']


def test_receivable_amount_and_date_must_be_the_ones_from_the_tool():
    case = 'pdf-03-yesterday-deposit'
    facts = {'amount': Decimal('3482.90'), 'due_date': date(2026, 12, 2)}
    right = good(case, answer='R$ 3.482,90, previsto para 02/12/2026. Dados fictícios de demonstração.')
    assert failed(case, right, facts=facts) == []
    assert failed(case, good(case, answer='R$ 3.482,90, previsto para 05/12/2026. Dados fictícios de demonstração.'), facts=facts) == ['facts_from_tool']
    assert failed(case, good(case, answer='R$ 3.482,90, previsto para 02/12/2026.'), facts=facts) == ['demo_notice']


def test_unknown_user_case_only_checks_the_http_status():
    assert metrics.score_case(CASES['neg-05-unknown-user'], {'status_code': 403})['passed']
    assert failed('neg-05-unknown-user', {'status_code': 200}) == ['http_status']


def _result(case_id, passed, tokens, latency, cost=None, invalid=0):
    return {'id': case_id, 'status': 'passed' if passed else 'failed', 'passed': passed, 'failed_checks': [] if passed else ['routing'],
            'checks': {'routing': passed, 'cites_expected': None}, 'http_status': 200, 'run_id': case_id, 'latency_ms': latency, 'error': None,
            'usage': {'input_tokens': tokens, 'output_tokens': 1, 'total_tokens': tokens + 1, 'cost_usd': cost}, 'cost_usd': cost,
            'citations': {'valid': 1, 'invalid': invalid, 'retrieved': 2}}


def test_aggregates_cover_routing_citations_latency_tokens_and_unknown_cost():
    rows = [_result('a', True, 99, 100.0), _result('b', False, 49, 300.0, invalid=1), _result('c', True, 9, 200.0),
            {'id': 'd', 'status': 'skipped', 'passed': None, 'reason': 'x'}]
    agg = metrics.aggregate(rows)
    assert (agg['cases_executed'], agg['cases_skipped'], agg['passed'], agg['failed']) == (3, 1, 2, 1)
    assert agg['checks']['routing'] == {'ok': 2, 'total': 3, 'rate': 2 / 3} and agg['checks']['cites_expected']['total'] == 0
    assert agg['citations'] == {'valid': 3, 'invalid': 1, 'validity_rate': 0.75, 'cases_with_invalid': 1}
    assert agg['latency_ms'] == {'p50': 200.0, 'p95': 300.0, 'samples': 3} and agg['tokens']['total_tokens'] == 160
    assert agg['cost_usd'] == {'known_total': None, 'cases_without_cost': 3}  # unknown stays unknown, never 0
    assert agg['failures'] == [{'id': 'b', 'failed_checks': ['routing'], 'error': None}]
    assert metrics.aggregate([_result('a', True, 9, 1.0, cost=0.5), _result('b', True, 9, 1.0)])['cost_usd'] == {'known_total': 0.5, 'cases_without_cost': 1}


def fake(tokens, fatal=False):
    async def observe_case(case):
        if fatal:
            return {'error': 'API inacessível', 'fatal': True}
        return {**good(case['id']), 'run_id': 'r', 'usage': {'total_tokens': tokens, 'cost_usd': None}}
    return observe_case


async def test_budget_limits_cases_and_stops_before_the_token_cap_would_be_exceeded():
    cases = list(CASES.values())[:4]
    capped = await run_dataset(cases, fake(100), max_cases=2)
    assert [r['status'] != 'skipped' for r in capped['cases']] == [True, True] and capped['budget']['aborted'] is None
    budget = await run_dataset(cases, fake(100), max_total_tokens=250)
    assert [r['status'] == 'skipped' for r in budget['cases']] == [False, False, True, True]  # a third case of 100 would reach 300
    assert budget['budget']['tokens_used'] == 200 <= 250 and budget['budget']['aborted_by'] == 'budget'
    assert 'orçamento' in budget['cases'][2]['reason']
    unlimited = await run_dataset(cases, fake(100))
    assert unlimited['budget']['tokens_used'] == 400 and unlimited['budget']['aborted'] is None


async def test_unreachable_api_stops_the_run_and_skips_the_rest():
    outcome = await run_dataset(list(CASES.values())[:3], fake(0, fatal=True))
    assert [r['status'] for r in outcome['cases']] == ['failed', 'skipped', 'skipped'] and outcome['budget']['aborted_by'] == 'error'


def test_report_files_share_a_timestamp_and_show_unknown_cost_as_not_informed(tmp_path):
    rows = [{**_result('a', True, 9, 100.0), 'agents': ['router'], 'tools': [], 'details': {}, 'answer': 'ok'}]
    data = {'mode': 'simulated', 'mode_label': 'simulada', 'started_at': '2026-11-03T12:00:00Z', 'finished_at': '2026-11-03T12:00:01Z',
            'dataset': {'path': 'd.jsonl', 'sha256': 'a' * 64, 'cases': 1, 'selected': 1},
            'budget': {'max_cases': None, 'max_total_tokens': None, 'tokens_used': 10, 'aborted': None}, 'cases': rows, 'aggregates': metrics.aggregate(rows)}
    json_path, md_path = report.write_report(data, tmp_path)
    assert (json_path.name, md_path.name) == ('20261103T120000Z.json', '20261103T120000Z.md')
    text = md_path.read_text(encoding='utf-8')
    assert 'não informado' in text and '| a | aprovado | router |' in text and 'Modo simulado' in text


@pytest.mark.parametrize('argv,message', [
    (['--real'], '--max-cases e --max-total-tokens'),
    (['--real', '--max-cases', '1'], '--max-cases e --max-total-tokens'),
    (['--base-url', 'http://localhost:8000'], '--base-url só vale com --real'),
    (['--max-cases', '0'], 'maiores que zero'),
    (['--only', 'nao-existe'], 'casos desconhecidos')])
async def test_cli_refuses_unsafe_or_invalid_options_before_doing_anything(argv, message, capsys):
    assert await amain(argv) == 2
    assert message in capsys.readouterr().err


async def test_real_mode_aborts_when_the_api_is_not_reachable_without_running_any_case(capsys, tmp_path):
    code = await amain(['--real', '--base-url', 'http://127.0.0.1:9', '--max-cases', '1', '--max-total-tokens', '10', '--out-dir', str(tmp_path)])
    assert code == 2 and 'inacessível' in capsys.readouterr().err and not list(tmp_path.iterdir())


async def test_simulated_mode_explains_a_missing_mongo_instead_of_crashing(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(simulated, 'MONGO_URI', 'mongodb://127.0.0.1:9/?directConnection=true')  # nothing listens there
    assert await amain(['--out-dir', str(tmp_path)]) == 2
    assert 'Mongo local indisponível' in capsys.readouterr().err and not list(tmp_path.iterdir())
