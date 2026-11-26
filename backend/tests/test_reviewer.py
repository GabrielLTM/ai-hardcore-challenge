"""Reviewer: graph rules, verdict schema, structured output, orchestrator review/revision and the API view of it."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `evaluation/` sits beside `src/`

import pytest  # noqa: E402
from app.adapters.outbound.fake.model import FakeModel, FakeModelSettings  # noqa: E402
from app.adapters.outbound.openai.gateway import OpenAIModelGateway  # noqa: E402
from app.application.use_cases.orchestration.orchestrator import ExecutionLimits, Orchestrator  # noqa: E402
from app.domain.graph import Node, check_graph  # noqa: E402
from app.domain.models import Agent, AgentKind, ModelEvent  # noqa: E402
from app.domain.review import VERDICT_SCHEMA, InvalidVerdict, parse_verdict  # noqa: E402
from evaluation.run_eval import parse_sse  # noqa: E402
from evaluation.scenarios import relay  # noqa: E402
from evaluation.simulated import Scenario, call, delegate, mongo_available, say, simulated_app, verdict  # noqa: E402
from test_core_orchestrator import CHUNK, WHO, Recorder, Scripted  # noqa: E402

SCORES = {'fundamentacao': 2, 'cobertura': 2, 'limitacoes': 2, 'seguranca': 2}
OK = json.dumps({'verdict': 'aprovar', 'scores': SCORES, 'issues': []})
BAD = json.dumps({'verdict': 'revisar', 'scores': {**SCORES, 'fundamentacao': 0}, 'issues': ['Prazo sem citação.']})


# ---------- domain ----------

def test_nobody_delegates_to_the_reviewer_and_the_reviewer_has_no_tools():
    nodes = {'router': Node(('reviewer',), ('delegate_to_agent',)), 'reviewer': Node(kind='reviewer')}
    assert [i.code for i in check_graph(nodes, root='router')] == ['reviewer_destination']
    armed = {'router': Node(), 'reviewer': Node((), ('search_knowledge',), kind='reviewer')}
    assert [i.code for i in check_graph(armed, root='router')] == ['reviewer_with_tools']
    assert check_graph({'router': Node(), 'reviewer': Node(kind='reviewer')}, root='router') == []


@pytest.mark.parametrize('text', [
    'não é json', '[]', json.dumps({'verdict': 'talvez', 'scores': SCORES, 'issues': []}),
    json.dumps({'verdict': 'aprovar', 'scores': {**SCORES, 'cobertura': 3}, 'issues': []}),
    json.dumps({'verdict': 'aprovar', 'scores': {**SCORES, 'cobertura': True}, 'issues': []}),
    json.dumps({'verdict': 'aprovar', 'scores': {'fundamentacao': 2}, 'issues': []}),
    json.dumps({'verdict': 'aprovar', 'scores': SCORES, 'issues': 'nenhum'})])
def test_off_schema_verdicts_are_rejected(text):
    with pytest.raises(InvalidVerdict):
        parse_verdict(text)


def test_a_valid_verdict_is_parsed():
    v = parse_verdict(BAD)
    assert (v.verdict, v.scores['fundamentacao'], v.issues) == ('revisar', 0, ['Prazo sem citação.'])
    assert set(VERDICT_SCHEMA['properties']['scores']['required']) == set(SCORES)


# ---------- providers ----------

async def test_openai_gateway_asks_for_strict_json_only_when_a_schema_is_given():
    sent = []

    class Stream:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration

    class Responses:
        async def create(self, **kwargs):
            sent.append(kwargs)
            return Stream()

    gateway = OpenAIModelGateway(type('Client', (), {'responses': Responses()})())
    [_ async for _ in gateway.stream(model='m', instructions='i', inputs=[], tools=[], response_schema=VERDICT_SCHEMA)]
    [_ async for _ in gateway.stream(model='m', instructions='i', inputs=[], tools=[])]
    assert sent[0]['text'] == {'format': {'type': 'json_schema', 'name': 'veredito', 'schema': VERDICT_SCHEMA, 'strict': True}}
    assert 'text' not in sent[1]


async def test_the_load_test_model_always_approves():
    fake = FakeModel(FakeModelSettings(first_token_ms=0, delta_ms=0))
    text = ''.join([e.text async for e in fake.stream(model='m', instructions='', inputs=[], tools=[], response_schema=VERDICT_SCHEMA)
                    if e.type == 'text_delta'])
    assert parse_verdict(text).verdict == 'aprovar'

    async def judge(question):
        content = json.dumps({'pergunta': question, 'resposta': 'x'}, ensure_ascii=False)
        events = fake.stream(model='m', instructions='', inputs=[{'content': content}], tools=[], response_schema=VERDICT_SCHEMA)
        return parse_verdict(''.join([e.text async for e in events if e.type == 'text_delta'])).verdict
    assert [await judge('Pix? #revisar'), await judge('Pix? #revisar')] == ['revisar', 'aprovar']  # demo hook: one revision


# ---------- orchestrator ----------

def agents(with_reviewer=True):
    out = {'router': Agent('router', 'Roteador', 'r', 'Regra', AgentKind.ROUTER, 'router', ('delegate_to_agent',), ('knowledge',)),
           'knowledge': Agent('knowledge', 'Especialista', 'k', 'Regra', AgentKind.KNOWLEDGE, 'knowledge', ('search_knowledge',)),
           'support': Agent('support', 'Suporte', 's', 'Regra', AgentKind.SUPPORT, 'support', ('get_receivables',))}
    if with_reviewer:
        out['reviewer'] = Agent('reviewer', 'Revisor', 'v', 'Julgue', AgentKind.REVIEWER, 'reviewer')
    return out


def turn(*events):
    return list(events)


def text(t): return [ModelEvent('text_delta', text=t), ModelEvent('completed', data={'output': [], 'usage': {'output_tokens': len(t) // 4}})]


def fn(name, **args):
    data = {'type': 'function_call', 'call_id': name, 'name': name, 'arguments': json.dumps(args)}
    return [ModelEvent('function_call', data=data), ModelEvent('completed', data={'output': [data], 'usage': None})]


async def search(identity, args): return {'results': [CHUNK]}
async def receivables(identity, args): return {'items': [{'amount': 10}]}
TOOLS = {'search_knowledge': search, 'get_receivables': receivables}


def knowledge_round(answer):
    """Router delegates, knowledge searches and answers, router relays."""
    return {'router': [fn('delegate_to_agent', agent_id='knowledge', task='Pix?'), text(answer)],
            'knowledge': [fn('search_knowledge', query='pix'), text(answer)]}


async def execute(model, graph=None, limits=None):
    rec = Recorder()
    orch = Orchestrator(model, graph or agents(), TOOLS, limits)
    return orch, rec, await orch.run('Pix cai quando?', WHO, rec)


async def test_an_answer_with_evidence_is_reviewed_and_approved():
    turns = knowledge_round('Varia conforme a modalidade [1].')
    model = Scripted(**turns, reviewer=[text(OK)])
    orch, rec, answer = await execute(model)
    assert answer == 'Varia conforme a modalidade [1].'
    assert rec.kinds()[-4:] == ['review.started', 'usage.recorded', 'review.completed', 'response.delta']  # text only after the verdict
    assert rec.of('response.delta') == [{'text': 'Varia conforme a modalidade [1].', 'agent_id': 'router'}]
    done = rec.of('review.completed')[0]
    assert (done['verdict'], done['round'], done['revision'], done['scores']) == ('aprovar', 1, 'not_needed', SCORES)
    sent = model.seen[-1]
    assert sent['extra']['response_schema'] == VERDICT_SCHEMA and sent['tools'] == []
    assert 'Prazo do Pix varia.' in sent['inputs'][0]['content'] and 'Pix cai quando?' in sent['inputs'][0]['content']
    assert orch.review == {'rounds': [{k: v for k, v in done.items() if k != 'revision'}], 'revision': 'not_needed', 'empty_searches': []}
    assert orch.answers == ['Varia conforme a modalidade [1].']  # the judged draft, kept apart from the run's `review`


async def test_the_reviewer_sees_recent_history_as_context():
    model = Scripted(**knowledge_round('Varia [1].'), reviewer=[text(OK)])
    history = [{'role': 'user', 'content': f'turno {i}'} for i in range(8)] + [{'role': 'assistant', 'content': 'Atendente Ana: use o Wi-Fi'}]
    await Orchestrator(model, agents(), TOOLS).run('E no Wi-Fi?', WHO, Recorder(), history=history)
    sent = model.seen[-1]['inputs'][0]['content']
    assert 'origem="historico_da_conversa"' in sent and 'Atendente Ana: use o Wi-Fi' in sent
    assert 'turno 2' not in sent and 'turno 3' in sent  # only the last six turns


async def test_a_customer_data_tool_also_triggers_the_review():
    model = Scripted(router=[fn('delegate_to_agent', agent_id='support', task='?'), text('R$ 10.')],
                     support=[fn('get_receivables'), text('R$ 10.')], reviewer=[text(OK)])
    graph = agents()
    graph['router'] = Agent('router', 'Roteador', 'r', 'Regra', AgentKind.ROUTER, 'router', ('delegate_to_agent',), ('support',))
    _, rec, _ = await execute(model, graph)
    assert rec.of('review.completed')[0]['verdict'] == 'aprovar'
    assert 'origem="dados_de_ferramentas"' in model.seen[-1]['inputs'][0]['content']  # the reviewer sees the customer data it judges
    assert '"get_receivables"' in model.seen[-1]['inputs'][0]['content']
    assert '"atendimento_humano_disponivel": false' in model.seen[-1]['inputs'][0]['content']  # no escalation agent in this graph


async def test_without_evidence_the_review_is_skipped_and_without_reviewer_nothing_happens():
    model = Scripted(router=[text('Olá!')])
    orch, rec, answer = await execute(model)
    assert answer == 'Olá!' and 'review.started' not in rec.kinds() and len(model.seen) == 1
    assert rec.of('review.completed') == [{'agent_id': 'reviewer', 'round': 1, 'verdict': 'skipped', 'revision': 'not_needed'}]
    plain = Scripted(**knowledge_round('Varia [1].'))
    orch, rec, _ = await execute(plain, agents(with_reviewer=False))
    assert not [k for k in rec.kinds() if k.startswith('review') or k == 'response.reset'] and orch.review is None


async def test_revise_runs_one_more_router_round_with_the_critique_and_stable_citations():
    first, second = knowledge_round('Cai em 1 dia.'), knowledge_round('Varia conforme a modalidade [1].')
    model = Scripted(router=first['router'] + second['router'], knowledge=first['knowledge'] + second['knowledge'],
                     reviewer=[text(BAD), text(OK)])
    orch, rec, answer = await execute(model)
    assert answer == 'Varia conforme a modalidade [1].'
    assert rec.of('response.delta') == [{'text': answer, 'agent_id': 'router'}]  # the rejected answer never reached the client
    assert 'response.reset' not in rec.kinds() and rec.kinds()[-1] == 'response.delta'
    assert rec.of('review.completed')[0]['agents'] == ['router', 'knowledge'] and rec.of('review.completed')[0]['sources'][0]['source_id'] == '04-pix'
    rounds = rec.of('review.completed')
    assert [(r['round'], r['verdict'], r['revision']) for r in rounds] == [(1, 'revisar', 'done'), (2, 'aprovar', 'done')]
    critique = model.seen[5]['inputs']  # router, knowledge x2, router, reviewer, then the second router call
    assert not [i for i in critique if i.get('role') == 'assistant']  # the rejected draft is not a turn the client saw
    assert 'Prazo sem citação.' in critique[-1]['content'] and 'Rascunho reprovado: Cai em 1 dia.' in critique[-1]['content']
    assert 'nunca viu' in critique[-1]['content']
    assert [i['evidence_id'] for i in orch.evidence.retrieved] == [1]  # same chunk, same number in both rounds
    assert orch.review['revision'] == 'done' and len(orch.review['rounds']) == 2


async def test_a_second_revise_is_delivered_without_a_third_round():
    first, second = knowledge_round('Cai em 1 dia.'), knowledge_round('Cai em 2 dias.')
    model = Scripted(router=first['router'] + second['router'], knowledge=first['knowledge'] + second['knowledge'],
                     reviewer=[text(BAD), text(BAD)])
    _, rec, answer = await execute(model)
    assert answer == 'Cai em 2 dias.' and rec.of('response.delta') == [{'text': 'Cai em 2 dias.', 'agent_id': 'router'}]
    assert [r['verdict'] for r in rec.of('review.completed')] == ['revisar', 'revisar']


async def test_no_budget_left_keeps_the_first_answer():
    model = Scripted(**knowledge_round('Cai em 1 dia, ' * 40), reviewer=[text(BAD)])
    _, rec, answer = await execute(model, limits=ExecutionLimits(max_output_tokens=300))
    assert answer.startswith('Cai em 1 dia') and 'response.reset' not in rec.kinds() and rec.of('response.delta')[0]['text'] == answer
    assert rec.of('review.completed')[0]['revision'] == 'skipped_budget'


@pytest.mark.parametrize('reply,code', [(text('{"verdict": "talvez"}'), 'invalid_verdict'),
                                        ([ModelEvent('error', data={})], 'model_error')])
async def test_a_failing_reviewer_never_blocks_the_answer(reply, code):
    model = Scripted(**knowledge_round('Varia [1].'), reviewer=[reply])
    _, rec, answer = await execute(model)
    done = rec.of('review.completed')[0]
    assert answer == 'Varia [1].' and (done['verdict'], done['code'], done['revision']) == ('failed', code, 'not_needed')


async def test_a_failed_revision_round_restores_the_first_answer():
    first = knowledge_round('Cai em 1 dia.')
    model = Scripted(router=first['router'] + [[ModelEvent('error', data={})]], knowledge=first['knowledge'], reviewer=[text(BAD)])
    orch, rec, answer = await execute(model)
    assert answer == 'Cai em 1 dia.' and orch.review['revision'] == 'failed'
    assert rec.of('response.delta') == [{'text': 'Cai em 1 dia.', 'agent_id': 'router'}] and 'response.reset' not in rec.kinds()


# ---------- API ----------

@pytest.mark.skipif(not mongo_available(), reason='Mongo local indisponível')
async def test_the_run_view_keeps_both_verdicts_and_the_conversation_only_the_final_answer():
    async with simulated_app() as sim:
        sim.use(Scenario(router=[delegate('knowledge', 'Pix?'), say(relay), delegate('knowledge', 'Pix?'), say(relay)],
                         knowledge=[call('search_knowledge', query='pix'), say('Cai em 1 dia.'),
                                    call('search_knowledge', query='pix'), say('Depende da modalidade [1].')],
                         reviewer=[verdict(fundamentacao=0), verdict('aprovar', ())], passages=('04-pix-comercial',)))
        body = (await sim.http.post('/api/v1/chat', json={'message': 'Pix cai quando?', 'user_id': 'cliente1988'})).json()
        assert body['answer'] == 'Depende da modalidade [1].' and not sim.gateway.errors
        run = (await sim.http.get(f"/api/v1/runs/{body['run_id']}")).json()
        assert [r['verdict'] for r in run['review']['rounds']] == ['revisar', 'aprovar'] and run['review']['revision'] == 'done'
        assert 'review_answers' not in run and 'Cai em 1 dia.' not in str(run)  # the rejected draft never reaches the customer
        stored = await sim.db.runs.find_one({'id': body['run_id']})
        assert stored['review_answers'] == ['Cai em 1 dia.', 'Depende da modalidade [1].']  # curation reads it
        events = parse_sse((await sim.http.get(f"/api/v1/runs/{body['run_id']}/events")).text)
        assert next(e for e in events if e['type'] == 'run.completed')['payload']['review']['revision'] == 'done'
        assert ''.join(e['payload']['text'] for e in events if e['type'] == 'response.delta') == 'Depende da modalidade [1].'
        cut = next(e['seq'] for e in events if e['type'] == 'review.started')  # reconnect while the text is held
        resumed = parse_sse((await sim.http.get(f"/api/v1/runs/{body['run_id']}/events", headers={'Last-Event-ID': str(cut)})).text)
        assert ''.join(e['payload']['text'] for e in resumed if e['type'] == 'response.delta') == 'Depende da modalidade [1].'
        messages = (await sim.http.get(f"/api/v1/conversations/{body['conversation_id']}/messages")).json()['items']
        assert [m['content'] for m in messages if m['role'] == 'assistant'] == ['Depende da modalidade [1].']


# ---------- evaluation report ----------

def test_review_aggregate_counts_first_round_approval_revisions_and_improvement():
    from evaluation.metrics import review_aggregate
    from evaluation.report import render_markdown
    low = {**SCORES, 'fundamentacao': 0}
    results = [
        {'id': 'a', 'review': {'rounds': [{'round': 1, 'verdict': 'aprovar', 'scores': SCORES}], 'revision': 'not_needed'}},
        {'id': 'b', 'review': {'rounds': [{'round': 1, 'verdict': 'revisar', 'scores': low},
                                          {'round': 2, 'verdict': 'aprovar', 'scores': SCORES}], 'revision': 'done'}},
        {'id': 'c', 'review': {'rounds': [], 'revision': 'not_needed'}},
        {'id': 'd', 'review': {'rounds': [{'round': 1, 'verdict': 'failed'}], 'revision': 'not_needed'}},
        {'id': 'e', 'review': None}]
    agg = review_aggregate(results)
    assert (agg['judged'], agg['skipped'], agg['failed']) == (2, 1, 1)
    assert (agg['first_round_approval'], agg['revision_rate'], agg['improved_after_revision']) == (0.5, 0.5, 1.0)
    assert agg['mean_scores']['fundamentacao'] == 2.0  # final answers only
    assert review_aggregate([])['first_round_approval'] is None
    report = {'mode': 'real', 'mode_label': 'real', 'started_at': 'x', 'finished_at': 'y',
              'dataset': {'path': 'd', 'cases': 1, 'sha256': '0' * 12, 'selected': 1},
              'budget': {'max_cases': None, 'tokens_used': 0, 'max_total_tokens': None, 'aborted': None},
              'cases': [{'id': 'b', 'status': 'passed', 'passed': True, 'agents': [], 'tools': [], 'citations': {'valid': 0, 'invalid': 0},
                         'latency_ms': 1, 'usage': {}, 'cost_usd': None, 'failed_checks': [], 'review': results[1]['review']}],
              'aggregates': {'cases_executed': 1, 'passed': 1, 'failed': 0, 'cases_skipped': 0, 'pass_rate': 1.0,
                             'latency_ms': {'p50': 1, 'p95': 1, 'samples': 1}, 'cost_usd': {'known_total': None, 'cases_without_cost': 0},
                             'tokens': {'total_tokens': 0, 'input_tokens': 0, 'output_tokens': 0, 'cases_without_usage': 0},
                             'citations': {'valid': 0, 'invalid': 0, 'validity_rate': None, 'cases_with_invalid': 0},
                             'checks': {}, 'failures': [], 'review': agg}}
    md = render_markdown(report)
    assert '## Revisor' in md and '| b | revisar → aprovar | done |' in md and 'Aprovadas na primeira rodada: 50%' in md


# ---------- calibration ----------

async def test_calibration_refuses_a_real_run_without_budget(tmp_path):
    from evaluation.calibrate_review import amain
    assert await amain(['--real', '--max-cases', '2', '--out-dir', str(tmp_path)]) == 2
    assert not list(tmp_path.iterdir())


async def test_calibration_measures_detection_specificity_and_the_criterion_hit(tmp_path):
    from evaluation.calibrate_review import amain, load_cases

    class Lenient:  # approves everything except the leak, and blames the wrong criterion
        async def stream(self, *, inputs, **_):
            leak = 'instruções internas' in inputs[0]['content']
            scores = {**SCORES, 'cobertura': 0} if leak else SCORES
            yield ModelEvent('text_delta', text=json.dumps({'verdict': 'revisar' if leak else 'aprovar', 'scores': scores, 'issues': []}))
            yield ModelEvent('completed', data={'output': [], 'usage': {'total_tokens': 100}})

    assert await amain(['--out-dir', str(tmp_path)], gateway=Lenient()) == 0
    report = json.loads(next(tmp_path.glob('review-*.json')).read_text())
    cases = load_cases()
    bad = sum(c['expect'] == 'revisar' for c in cases)
    s = report['summary']
    assert (s['recall_revisar'], s['specificity'], s['criterion_hit_rate']) == (1 / bad, 1.0, 0.0)
    assert s['by_defect']['leak'] == {'cases': 1, 'detected': 1, 'criterion_hit': 0} and s['tokens'] == 100 * len(cases)
    capped = tmp_path / 'capped'
    assert await amain(['--out-dir', str(capped), '--max-total-tokens', '250'], gateway=Lenient()) == 0
    assert len(json.loads(next(capped.glob('review-*.json')).read_text())['results']) == 2


async def test_a_greeting_streams_live_and_a_preface_sent_before_the_evidence_is_reset():
    model = Scripted(router=[[ModelEvent('text_delta', text='Ol'), ModelEvent('text_delta', text='á!'),
                              ModelEvent('completed', data={'output': [], 'usage': None})]])
    _, rec, _ = await execute(model)
    assert [d['text'] for d in rec.of('response.delta')] == ['Ol', 'á!']  # live, token by token
    preface = [ModelEvent('text_delta', text='Vou verificar. ')] + fn('delegate_to_agent', agent_id='knowledge', task='Pix?')
    model = Scripted(router=[preface, text('Varia [1].')], knowledge=[fn('search_knowledge', query='pix'), text('Varia [1].')],
                     reviewer=[text(OK)])
    _, rec, answer = await execute(model)
    kinds = rec.kinds()
    assert kinds.index('response.reset') > kinds.index('review.completed') and rec.of('response.delta')[-1]['text'] == answer


async def test_web_evidence_keeps_the_text_the_search_returned_for_the_reviewer():
    async def web(identity, args):
        return {'answer': 'Previsão: 16 a 24 graus.', 'sources': [{'url': 'https://clima.test', 'title': 'Clima'}], 'retrieved_at': '2026-11-25T10:00:00Z'}
    graph = agents()
    graph['knowledge'] = Agent('knowledge', 'Especialista', 'k', 'Regra', AgentKind.KNOWLEDGE, 'knowledge', ('web_search',))
    model = Scripted(router=[fn('delegate_to_agent', agent_id='knowledge', task='Clima?'), text('16 a 24 graus [1].')],
                     knowledge=[fn('web_search', query='clima'), text('16 a 24 graus [1].')], reviewer=[text(OK)])
    rec = Recorder()
    orch = Orchestrator(model, graph, {'web_search': web}, None)
    await orch.run('Clima amanhã?', WHO, rec)
    assert 'Previsão: 16 a 24 graus.' in model.seen[-1]['inputs'][0]['content']
    assert 'Previsão' not in json.dumps(rec.of('retrieval.completed'))  # the text stays out of the events


async def test_empty_knowledge_searches_are_kept_for_curation():
    async def nothing(identity, args): return {'results': []}
    model = Scripted(router=[fn('delegate_to_agent', agent_id='knowledge', task='?'), text('Não encontrei.')],
                     knowledge=[fn('search_knowledge', query='taxa 2027'), text('Não encontrei.')])
    rec = Recorder()
    orch = Orchestrator(model, agents(), {'search_knowledge': nothing}, None)
    await orch.run('Taxa de 2027?', WHO, rec)
    assert orch.review == {'rounds': [], 'revision': 'not_needed', 'empty_searches': ['taxa 2027']}


# ---------- curation ----------

def record(verdict, scores=None, agents=('router', 'knowledge'), empty=(), question='Pix?', run='r'):
    rounds = [] if verdict is None else [{'round': 1, 'verdict': verdict, 'scores': scores or SCORES, 'issues': ['Sem citação.'] if verdict == 'revisar' else [],
                                           'agents': list(agents), 'sources': [{'source_id': '04-pix', 'title': 'Pix'}]}]
    return {'run_id': run, 'conversation_id': 'c', 'created_at': '2026-11-25T10:00:00Z', 'question': question,
            'review': {'rounds': rounds, 'revision': 'done' if verdict == 'revisar' else 'not_needed', 'empty_searches': list(empty)}}


def test_curation_finds_failing_criteria_agents_empty_searches_and_suggests_fixes():
    from app.application.use_cases.curation import summarize_reviews
    low = {**SCORES, 'fundamentacao': 0}
    out = summarize_reviews([record('revisar', low, run='r1', question='Taxa?'), record('aprovar', run='r2'),
                             record('aprovar', agents=('router', 'support'), run='r3'),
                             record(None, empty=('taxa 2027',), run='r4'), record(None, empty=('taxa 2027',), run='r5')], days=7)
    assert (out['runs'], out['judged'], out['revised'], out['skipped'], out['first_round_approval']) == (5, 3, 1, 2, 2 / 3)
    assert out['criteria'][0] == {'criterion': 'fundamentacao', 'label': 'Fundamentação', 'below_max': 1, 'rate': 1 / 3}
    assert [a['agent_id'] for a in out['agents']] == ['knowledge', 'router', 'support']  # highest revision rate first
    knowledge = next(a for a in out['agents'] if a['agent_id'] == 'knowledge')
    assert (knowledge['judged'], knowledge['revised'], knowledge['rate']) == (2, 1, 0.5)
    assert out['empty_searches'] == [{'query': 'taxa 2027', 'count': 2, 'last_at': '2026-11-25T10:00:00Z'}]
    assert [e['question'] for e in out['examples']] == ['Taxa?'] and out['examples'][0]['issues'] == ['Sem citação.']
    kinds = [(s['kind'], s['target']) for s in out['suggestions']]
    assert kinds == [('search', 'knowledge'), ('knowledge', 'taxa 2027')]
    assert summarize_reviews([], 7)['first_round_approval'] is None
    assert out['examples'][0]['failed'] == ['fundamentacao'] and (out['redone'], out['fixed']) == (0, 0)
    redone = record('revisar', low, run='r6')
    redone['review']['rounds'].append({**redone['review']['rounds'][0], 'round': 2, 'verdict': 'aprovar'})
    redone['review']['rounds'][0]['sources'] *= 2  # the same source twice
    redone['answers'] = ['rascunho', 'final']
    out = summarize_reviews([redone], 7)
    assert (out['redone'], out['fixed']) == (1, 1) and len(out['examples'][0]['sources']) == 1
    assert out['examples'][0]['answers'] == ['rascunho', 'final']
    one_of_one = [record('revisar', low, agents=('router', 'escalation'), run='e')]
    many = [record('revisar', low, run=f'k{i}') for i in range(3)] + [record('aprovar', run='k9')]
    assert summarize_reviews(one_of_one + many, 7)['suggestions'][0]['target'] == 'knowledge'  # volume beats a 1/1 rate


@pytest.mark.skipif(not mongo_available(), reason='Mongo local indisponível')
async def test_curation_endpoint_is_admin_only_and_reads_the_workspace_runs():
    from app.adapters.inbound.http.agents_routes import current_identity
    from app.adapters.inbound.http.curation_routes import router as curation_router
    from app.domain.identity import Identity
    async with simulated_app() as sim:
        sim.app.include_router(curation_router)
        sim.use(Scenario(router=[delegate('knowledge', 'Pix?'), say(relay), delegate('knowledge', 'Pix?'), say(relay)],
                         knowledge=[call('search_knowledge', query='pix'), say('Cai em 1 dia.'),
                                    call('search_knowledge', query='pix'), say('Depende da modalidade [1].')],
                         reviewer=[verdict(fundamentacao=0), verdict('aprovar', ())], passages=('04-pix-comercial',)))
        await sim.http.post('/api/v1/chat', json={'message': 'Pix cai quando?', 'user_id': 'cliente1988'})
        out = (await sim.http.get('/api/v1/curation/reviews?days=7')).json()
        assert (out['judged'], out['revised']) == (1, 1) and out['examples'][0]['question'] == 'Pix cai quando?'
        assert out['examples'][0]['agents'] == ['router', 'knowledge'] and out['criteria'][0]['criterion'] == 'fundamentacao'
        sim.app.dependency_overrides[current_identity] = lambda: Identity('outro', 'outro-ws', 'admin')
        assert (await sim.http.get('/api/v1/curation/reviews')).json()['runs'] == 0  # another workspace sees nothing
        sim.app.dependency_overrides[current_identity] = lambda: Identity('cliente1988', 'demo', 'operator')
        response = await sim.http.get('/api/v1/curation/reviews')
        assert response.status_code == 403 and response.json()['code'] == 'forbidden'
