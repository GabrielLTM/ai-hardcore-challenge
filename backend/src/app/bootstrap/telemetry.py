"""Telemetry wiring. Must never block or fail the support flow.

Privacy: spans, metrics and logs carry only identifiers, names, counts, durations, tokens and cost.
Message text, tool arguments/results and retrieved passages are never read from event payloads.
"""
import asyncio
import logging
import time
from contextlib import nullcontext
from dataclasses import dataclass
from opentelemetry import context as otel_context, trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from app.bootstrap.settings import Settings
from app.domain.pricing import PRICING_VERSION, estimate_cost_usd

SERVICE_NAME = 'getnet-support-api'
log = logging.getLogger(__name__)  # degradation notices only, never content
_SECONDS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120]
_TERMINAL = {'run.completed': 'completed', 'run.failed': 'failed', 'run.cancelled': 'cancelled', 'run.interrupted': 'interrupted'}
# Allow-list of payload fields that may reach logs (payload key -> log attribute). Everything else is dropped.
_LOG_FIELDS = {'agent_id': 'agent_id', 'name': 'agent_name', 'version': 'agent_version', 'tool': 'tool',
               'destination': 'destination', 'model': 'model'}
_UNTRACED_PREFIX = '/api/v1/health/'


def _safe(fn, *args, **kwargs):
    """Run a telemetry action; any failure is logged without detail and never reaches the caller."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log.warning('Telemetria degradada: %s', type(exc).__name__)


def _clean(attrs: dict) -> dict:
    return {k: v for k, v in attrs.items() if v is not None}


class _Telemetry:
    def __init__(self, span_processors, metric_readers, log_processors, version: str):
        resource = Resource.create({'service.name': SERVICE_NAME, 'service.version': version})
        self.tracer_provider = TracerProvider(resource=resource)
        for processor in span_processors:
            self.tracer_provider.add_span_processor(processor)
        self.meter_provider = MeterProvider(resource=resource, metric_readers=list(metric_readers))
        self.logger_provider = LoggerProvider(resource=resource)
        for processor in log_processors:
            self.logger_provider.add_log_record_processor(processor)
        self.logger = self.logger_provider.get_logger('app.run')  # sanitized structured run events
        self.tracer = self.tracer_provider.get_tracer(__name__)
        meter = self.meter_provider.get_meter(__name__)
        self.http_duration = meter.create_histogram('http.server.request.duration', 's', 'Duração das requisições HTTP',
                                                    explicit_bucket_boundaries_advisory=_SECONDS)
        self.runs = meter.create_counter('getnet.runs', '{run}', 'Execuções por status final')
        self.run_duration = meter.create_histogram('getnet.run.duration', 's', 'Duração das execuções',
                                                   explicit_bucket_boundaries_advisory=_SECONDS)
        self.agent_duration = meter.create_histogram('getnet.agent.duration', 's', 'Duração por agente',
                                                     explicit_bucket_boundaries_advisory=_SECONDS)
        self.tool_duration = meter.create_histogram('getnet.tool.duration', 's', 'Duração por ferramenta',
                                                    explicit_bucket_boundaries_advisory=_SECONDS)
        self.tokens = meter.create_counter('getnet.llm.tokens', '{token}', 'Tokens informados pelo provedor')
        # No unit: Prometheus would append it to the name; "usd" is already in the name.
        self.cost = meter.create_counter('getnet.llm.cost.usd', '', 'Custo estimado (USD) com preço versionado')
        self.unpriced = meter.create_counter('getnet.llm.unpriced.calls', '{call}', 'Chamadas sem custo estimável')

    def shutdown(self):
        for provider in (self.tracer_provider, self.meter_provider, self.logger_provider):
            _safe(provider.shutdown)


_T: _Telemetry | None = None


def _configure(span_processors, metric_readers, log_processors, version: str) -> _Telemetry:
    global _T
    old, _T = _T, _Telemetry(span_processors, metric_readers, log_processors, version)
    if old:
        _safe(old.shutdown)
    return _T


def setup_telemetry(app, settings: Settings) -> None:
    """Configure tracer, meter and logs (exported over OTLP/HTTP when an endpoint is set) and add the HTTP middleware."""
    try:
        endpoint = (settings.otel_exporter_otlp_endpoint or '').rstrip('/')
        spans, readers, logs = [], [], []
        if endpoint:  # short timeouts: an unreachable collector must not stall the background exporters
            spans = [BatchSpanProcessor(OTLPSpanExporter(endpoint=f'{endpoint}/v1/traces', timeout=2))]
            readers = [PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f'{endpoint}/v1/metrics', timeout=2),
                                                     export_interval_millis=10_000)]
            logs = [BatchLogRecordProcessor(OTLPLogExporter(endpoint=f'{endpoint}/v1/logs', timeout=2))]
        _configure(spans, readers, logs, app.version)
        app.middleware('http')(_http_middleware)
    except Exception as exc:
        log.warning('Telemetria desativada: %s', type(exc).__name__)


async def _http_middleware(request, call_next):
    tel = _T
    if tel is None or request.url.path.startswith(_UNTRACED_PREFIX):  # compose health checks would drown real traffic
        return await call_next(request)
    started = time.perf_counter()
    span = _safe(tel.tracer.start_span, f'{request.method} {request.url.path}', kind=SpanKind.SERVER)
    status = 500
    try:
        with trace.use_span(span, end_on_exit=False) if span is not None else nullcontext():
            response = await call_next(request)
        status = response.status_code
        if span is not None:
            _safe(response.headers.__setitem__, 'X-Trace-ID', format(span.get_span_context().trace_id, '032x'))
        return response
    finally:
        _safe(_finish_http, tel, span, request, status, time.perf_counter() - started)


def _finish_http(tel: _Telemetry, span, request, status: int, elapsed: float) -> None:
    route = getattr(request.scope.get('route'), 'path', None) or 'unmatched'  # template, not raw path: low cardinality
    attrs = {'http.request.method': request.method, 'http.route': route, 'http.response.status_code': status}
    tel.http_duration.record(elapsed, attrs)
    if span is not None:
        span.update_name(f'{request.method} {route}')
        span.set_attributes(attrs)
        if status >= 500:
            span.set_status(Status(StatusCode.ERROR))
        span.end()


@dataclass(slots=True)
class _Open:
    kind: str  # 'agent' | 'tool'
    key: str  # agent id or tool name
    span: trace.Span
    started: float


class _RunTrace:
    """Spans, metrics and logs of one run. Events arrive sequentially, so a stack mirrors agent/tool nesting."""

    def __init__(self, tel: _Telemetry, run_id: str):
        self.tel, self.run_id = tel, run_id
        self.started = time.perf_counter()
        # Parent = request span active when the run was created, so the run joins the HTTP request's trace.
        self.run_span = tel.tracer.start_span('run', context=otel_context.get_current(), attributes={'run.id': run_id})
        self.stack: list[_Open] = []
        self.done = False

    @property
    def trace_id(self) -> str:
        return format(self.run_span.get_span_context().trace_id, '032x')

    def _top(self) -> trace.Span:
        return self.stack[-1].span if self.stack else self.run_span

    def _start(self, kind: str, key: str, name: str, attrs: dict) -> None:
        span = self.tel.tracer.start_span(name, context=trace.set_span_in_context(self._top()), attributes=_clean(attrs))
        self.stack.append(_Open(kind, key, span, time.perf_counter()))

    def _close(self, opened: _Open, outcome: str) -> None:
        histogram = self.tel.agent_duration if opened.kind == 'agent' else self.tel.tool_duration
        histogram.record(time.perf_counter() - opened.started, {opened.kind: opened.key, 'status': outcome})
        if outcome == 'error':
            opened.span.set_status(Status(StatusCode.ERROR))
        opened.span.set_attribute('outcome', outcome)
        opened.span.end()

    def _end(self, kind: str, key: str, ok: bool) -> None:
        index = next((i for i in range(len(self.stack) - 1, -1, -1)
                      if self.stack[i].kind == kind and self.stack[i].key == key), None)
        if index is None:
            return
        while len(self.stack) > index:  # children that never reported their end are closed as errors
            opened = self.stack.pop()
            self._close(opened, ('ok' if ok else 'error') if len(self.stack) == index else 'error')

    def record(self, kind: str, data: dict) -> None:
        if self.done:
            return
        agent_id, tool = data.get('agent_id'), data.get('tool')
        extra: dict = {}
        if kind == 'agent.started':
            self._start('agent', agent_id, f"agent {data.get('name') or agent_id}",
                        {'agent.id': agent_id, 'agent.name': data.get('name'), 'agent.version': data.get('version')})
        elif kind in ('agent.completed', 'agent.failed'):
            self._end('agent', agent_id, kind.endswith('completed'))
        elif kind == 'tool.started':
            self._start('tool', tool, f'tool {tool}', {'tool.name': tool, 'agent.id': agent_id})
        elif kind in ('tool.completed', 'tool.failed'):
            self._end('tool', tool, kind.endswith('completed'))
        elif kind == 'route.selected':
            self._top().add_event('route.selected', _clean({'agent.id': agent_id, 'route.destination': data.get('destination')}))
        elif kind in ('retrieval.completed', 'web.completed'):  # count only; passages and URLs stay out of telemetry
            self._top().add_event(kind, {'count': len(data.get('results') or data.get('sources') or [])})
        elif kind == 'usage.recorded':
            extra = self._usage(data)
        elif kind in _TERMINAL:
            self.finish(_TERMINAL[kind])
            return
        elif kind == 'response.delta':  # message content: neither logged nor traced
            return
        self._log(kind, data, extra)

    def _usage(self, data: dict) -> dict:
        model = data.get('model') or 'unknown'
        usage = data.get('usage') or {}
        tokens_in = usage.get('input_tokens', usage.get('prompt_tokens'))
        tokens_out = usage.get('output_tokens', usage.get('completion_tokens'))
        cost = data['cost_usd'] if 'cost_usd' in data else estimate_cost_usd(model, usage)
        for direction, tokens in (('input', tokens_in), ('output', tokens_out)):
            if tokens is not None:
                self.tel.tokens.add(tokens, {'model': model, 'direction': direction})
        if cost is None:  # unknown stays unknown: counted separately, never recorded as zero cost
            self.tel.unpriced.add(1, {'model': model})
        else:
            self.tel.cost.add(cost, {'model': model})
        attrs = _clean({'gen_ai.request.model': model, 'gen_ai.usage.input_tokens': tokens_in,
                        'gen_ai.usage.output_tokens': tokens_out, 'getnet.llm.cost_usd': cost,
                        'getnet.llm.cost_known': cost is not None, 'getnet.pricing_version': data.get('pricing_version', PRICING_VERSION)})
        self._top().set_attributes(attrs)
        return _clean({'tokens_in': tokens_in, 'tokens_out': tokens_out, 'cost_usd': cost})

    def _log(self, kind: str, data: dict, extra: dict | None = None) -> None:
        fields = {attr: data[key] for key, attr in _LOG_FIELDS.items() if isinstance(data.get(key), (str, int, float, bool))}
        self.tel.logger.emit(context=trace.set_span_in_context(self._top()), severity_number=SeverityNumber.INFO,
                             severity_text='INFO', body=kind,
                             attributes={'run_id': self.run_id, 'event': kind, **fields, **(extra or {})})

    def finish(self, status: str) -> None:
        if self.done:
            return
        self.done = True
        while self.stack:
            self._close(self.stack.pop(), {'completed': 'ok', 'cancelled': 'cancelled'}.get(status, 'error'))
        elapsed = time.perf_counter() - self.started
        self.run_span.set_attribute('run.status', status)
        if status in ('failed', 'interrupted'):
            self.run_span.set_status(Status(StatusCode.ERROR))
        self.run_span.end()
        self.tel.runs.add(1, {'status': status})
        self.tel.run_duration.record(elapsed, {'status': status})
        self._log(f'run.{status}', {}, {'status': status, 'duration_ms': round(elapsed * 1000)})


def traced_emit(emit, run_id: str):
    """Wrap a run's event emitter to record spans, metrics and estimated cost per event.

    Returns `emit` unchanged when telemetry is not configured. The wrapper adds `trace_id` to the `run.started`
    payload and re-raises whatever the original emit raises (cancellation included). Terminal events
    (`run.completed|failed|cancelled|interrupted`) close the run when they pass through the wrapper; callers
    that persist them another way must call `wrapped.finish(status)` instead.
    """
    run = _safe(_RunTrace, _T, run_id) if _T else None
    if run is None:
        return emit

    async def wrapped(kind, data):
        if kind == 'run.started':
            data = _safe(lambda: {**data, 'trace_id': run.trace_id}) or data
        try:
            result = await emit(kind, data)
        except BaseException as exc:
            _safe(run.finish, 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed')
            raise
        _safe(run.record, kind, data)
        return result

    wrapped.finish = lambda status: _safe(run.finish, status)
    return wrapped
