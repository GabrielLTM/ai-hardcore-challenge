import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from app.bootstrap import telemetry

SECRET = 'SEGREDO-NAO-VAZAR'


class Collected:
    def __init__(self):
        self.spans, self.reader, self.logs = InMemorySpanExporter(), InMemoryMetricReader(), InMemoryLogRecordExporter()

    def by_name(self):
        return {s.name: s for s in self.spans.get_finished_spans()}

    def metrics(self):
        out = {}
        for rm in self.reader.get_metrics_data().resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    for point in metric.data.data_points:
                        value = getattr(point, 'value', None)
                        out[(metric.name, tuple(sorted(point.attributes.items())))] = point.count if value is None else value
        return out

    def everything(self):
        spans = [(s.name, dict(s.attributes), [(e.name, dict(e.attributes)) for e in s.events]) for s in self.spans.get_finished_spans()]
        logs = [(r.log_record.body, dict(r.log_record.attributes)) for r in self.logs.get_finished_logs()]
        return repr(spans) + repr(logs)


@pytest.fixture
def collected(monkeypatch):
    monkeypatch.setattr(telemetry, '_T', None)
    c = Collected()
    tel = telemetry._configure([SimpleSpanProcessor(c.spans)], [c.reader], [SimpleLogRecordProcessor(c.logs)], '0')
    yield c
    tel.shutdown()


async def play(emit, events):
    for kind, data in events:
        await emit(kind, data)


FLOW = [
    ('run.started', {}),
    ('agent.started', {'agent_id': 'router', 'name': 'Roteador', 'version': 1}),
    ('usage.recorded', {'agent_id': 'router', 'model': 'gpt-4o-mini', 'usage': {'input_tokens': 100, 'output_tokens': 20}}),
    ('tool.started', {'agent_id': 'router', 'tool': 'delegate_to_agent', 'arguments': SECRET}),
    ('route.selected', {'agent_id': 'router', 'destination': 'knowledge'}),
    ('agent.started', {'agent_id': 'knowledge', 'name': 'Especialista Getnet', 'version': 2, 'parent_agent_id': 'router'}),
    ('tool.started', {'agent_id': 'knowledge', 'tool': 'search_knowledge'}),
    ('retrieval.completed', {'agent_id': 'knowledge', 'results': [{'text': SECRET}]}),
    ('tool.completed', {'agent_id': 'knowledge', 'tool': 'search_knowledge'}),
    ('usage.recorded', {'agent_id': 'knowledge', 'model': 'modelo-sem-preco', 'usage': {'input_tokens': 50, 'output_tokens': 10}}),
    ('response.delta', {'text': SECRET, 'agent_id': 'router'}),
    ('agent.completed', {'agent_id': 'knowledge', 'version': 2}),
    ('tool.completed', {'agent_id': 'router', 'tool': 'delegate_to_agent'}),
    ('agent.completed', {'agent_id': 'router', 'version': 1}),
    ('run.completed', {}),
]


async def test_run_produces_hierarchical_spans_metrics_and_clean_logs(collected):
    seen = []

    async def emit(kind, data):
        seen.append((kind, data))

    wrapped = telemetry.traced_emit(emit, 'run-1')
    await play(wrapped, FLOW)

    assert [k for k, _ in seen] == [k for k, _ in FLOW]  # every event reaches the original emit, in order
    spans = collected.by_name()
    run, router, delegate = spans['run'], spans['agent Roteador'], spans['tool delegate_to_agent']
    knowledge, search = spans['agent Especialista Getnet'], spans['tool search_knowledge']
    assert run.parent is None
    assert router.parent.span_id == run.context.span_id
    assert delegate.parent.span_id == router.context.span_id
    assert knowledge.parent.span_id == delegate.context.span_id
    assert search.parent.span_id == knowledge.context.span_id
    assert len({s.context.trace_id for s in spans.values()}) == 1
    trace_id = format(run.context.trace_id, '032x')
    assert seen[0] == ('run.started', {'trace_id': trace_id})
    assert any(e.name == 'route.selected' for e in delegate.events)

    metrics = collected.metrics()
    assert metrics[('getnet.runs', (('status', 'completed'),))] == 1
    assert metrics[('getnet.llm.tokens', (('direction', 'input'), ('model', 'gpt-4o-mini')))] == 100
    assert metrics[('getnet.llm.tokens', (('direction', 'output'), ('model', 'modelo-sem-preco')))] == 10
    assert metrics[('getnet.llm.cost.usd', (('model', 'gpt-4o-mini'),))] == pytest.approx((100 * 0.15 + 20 * 0.60) / 1e6)
    assert metrics[('getnet.llm.unpriced.calls', (('model', 'modelo-sem-preco'),))] == 1
    assert not any(name == 'getnet.llm.cost.usd' and dict(a).get('model') == 'modelo-sem-preco' for name, a in metrics)
    assert router.attributes['getnet.llm.cost_known'] is True and knowledge.attributes['getnet.llm.cost_known'] is False
    assert 'getnet.llm.cost_usd' not in knowledge.attributes

    assert SECRET not in collected.everything()
    logs = [r.log_record for r in collected.logs.get_finished_logs()]
    assert {r.attributes['event'] for r in logs} >= {'agent.started', 'tool.completed', 'usage.recorded', 'run.completed'}
    assert all(r.attributes['run_id'] == 'run-1' and r.trace_id for r in logs)


async def test_cancellation_is_propagated_and_closes_open_spans(collected):
    async def emit(kind, data):
        if kind == 'tool.started':
            raise asyncio.CancelledError()

    wrapped = telemetry.traced_emit(emit, 'run-2')
    await wrapped('agent.started', {'agent_id': 'router', 'name': 'Roteador'})
    with pytest.raises(asyncio.CancelledError):
        await wrapped('tool.started', {'agent_id': 'router', 'tool': 'x'})
    spans = collected.by_name()
    assert spans['run'].attributes['run.status'] == 'cancelled'
    assert spans['agent Roteador'].attributes['outcome'] == 'cancelled'
    assert collected.metrics()[('getnet.runs', (('status', 'cancelled'),))] == 1


async def test_finish_closes_run_for_callers_that_persist_terminal_events_themselves(collected):
    wrapped = telemetry.traced_emit(lambda k, d: asyncio.sleep(0), 'run-3')
    await wrapped('agent.started', {'agent_id': 'router', 'name': 'Roteador'})
    wrapped.finish('failed')
    wrapped.finish('failed')  # idempotent
    spans = collected.by_name()
    assert spans['run'].status.status_code.name == 'ERROR'
    assert spans['agent Roteador'].status.status_code.name == 'ERROR'
    assert collected.metrics()[('getnet.runs', (('status', 'failed'),))] == 1


def test_http_middleware_sets_trace_header_and_records_route_template(collected):
    app = FastAPI()

    @app.get('/api/v1/items/{item_id}')
    async def item(item_id: str):
        return {'id': item_id}

    @app.get('/api/v1/health/live')
    async def live():
        return {'status': 'ok'}

    app.middleware('http')(telemetry._http_middleware)
    client = TestClient(app)
    assert 'x-trace-id' not in client.get('/api/v1/health/live').headers
    response = client.get('/api/v1/items/abc')
    assert client.get('/api/v1/nao-existe').status_code == 404
    trace_id = response.headers['x-trace-id']
    span = collected.by_name()['GET /api/v1/items/{item_id}']
    assert trace_id == format(span.context.trace_id, '032x')
    assert span.attributes['http.response.status_code'] == 200
    metrics = collected.metrics()
    assert metrics[('http.server.request.duration', (('http.request.method', 'GET'), ('http.response.status_code', 200),
                                                      ('http.route', '/api/v1/items/{item_id}')))] == 1
    assert not any('abc' in repr(key) for key in metrics)  # no raw paths in metric labels
    assert any(dict(a).get('http.route') == 'unmatched' for name, a in metrics if name == 'http.server.request.duration')
