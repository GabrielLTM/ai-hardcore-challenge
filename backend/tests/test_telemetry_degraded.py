"""Telemetry failures (dead collector, broken internals) must never affect the support flow."""
import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.bootstrap import telemetry
from app.bootstrap.settings import Settings

DEAD_COLLECTOR = 'http://127.0.0.1:9'  # discard port: connection refused


@pytest.fixture
def dead_collector(monkeypatch):
    monkeypatch.setattr(telemetry, '_T', None)
    app = FastAPI()

    @app.get('/api/v1/ping')
    async def ping():
        return {'ok': True}

    telemetry.setup_telemetry(app, Settings(_env_file=None, otel_exporter_otlp_endpoint=DEAD_COLLECTOR))
    yield app
    started = time.monotonic()
    telemetry._T.shutdown()  # flushing to a dead collector must stay bounded
    assert time.monotonic() - started < 15


async def test_traced_emit_forwards_every_event_with_dead_collector(dead_collector):
    seen = []

    async def emit(kind, data):
        seen.append((kind, data))

    wrapped = telemetry.traced_emit(emit, 'run-1')
    events = [('run.started', {}), ('agent.started', {'agent_id': 'router', 'name': 'Roteador'}),
              ('usage.recorded', {'agent_id': 'router', 'model': 'gpt-6-luna', 'usage': None}),
              ('agent.completed', {'agent_id': 'router'}), ('run.completed', {})]
    for kind, data in events:
        await wrapped(kind, data)
    assert [k for k, _ in seen] == [k for k, _ in events]
    assert len(seen[0][1]['trace_id']) == 32


def test_http_still_answers_with_dead_collector(dead_collector):
    response = TestClient(dead_collector).get('/api/v1/ping')
    assert response.status_code == 200 and response.json() == {'ok': True}
    assert len(response.headers['x-trace-id']) == 32


async def test_broken_telemetry_internals_do_not_reach_the_flow(dead_collector, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('telemetria quebrada')

    monkeypatch.setattr(telemetry._RunTrace, 'record', boom)
    monkeypatch.setattr(telemetry._RunTrace, 'finish', boom)
    monkeypatch.setattr(telemetry._RunTrace, 'trace_id', property(boom))
    seen = []

    async def emit(kind, data):
        seen.append((kind, data))
        return kind

    wrapped = telemetry.traced_emit(emit, 'run-2')
    assert await wrapped('run.started', {}) == 'run.started'  # original payload and result untouched
    await wrapped('run.completed', {})
    assert seen == [('run.started', {}), ('run.completed', {})]


async def test_original_emit_errors_still_propagate(dead_collector):
    async def emit(kind, data):
        raise ValueError('falha do emit')

    with pytest.raises(ValueError):
        await telemetry.traced_emit(emit, 'run-3')('agent.started', {'agent_id': 'router'})


async def test_unconfigured_telemetry_returns_emit_unchanged(monkeypatch):
    monkeypatch.setattr(telemetry, '_T', None)

    async def emit(kind, data): ...

    assert telemetry.traced_emit(emit, 'run-4') is emit
