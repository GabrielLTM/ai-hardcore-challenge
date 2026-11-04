"""The canned load-test model: same turn structure as a real run, no network, no invented usage or cost."""
from app.adapters.inbound.http.execution import default_runtime
from app.adapters.outbound.fake.model import FakeModel, FakeModelSettings
from app.application.use_cases.orchestrator import Orchestrator
from app.application.use_cases.tools import make_tools
from app.bootstrap.settings import Settings
from app.domain.identity import Identity
from app.domain.models import Agent, AgentKind

AGENTS = {'router': Agent('router', 'Roteador', 'Roteia', 'Regra', AgentKind.ROUTER, 'mock', ('delegate_to_agent',), ('knowledge',)),
          'knowledge': Agent('knowledge', 'Especialista', 'Responde', 'Regra', AgentKind.KNOWLEDGE, 'mock', ('search_knowledge',), ())}


async def run_fake(**cfg):
    fake, events = FakeModel(FakeModelSettings(**cfg, _env_file=None)), []

    async def emit(kind, data): events.append((kind, data))
    orchestrator = Orchestrator(fake, AGENTS, make_tools(None, fake, fake, 'emb', None))
    answer = await orchestrator.run('Qual o prazo do Pix?', Identity('u', 'demo', 'operator'), emit)
    return answer, events, orchestrator


async def test_fake_run_delegates_retrieves_and_cites_a_valid_source():
    answer, events, orchestrator = await run_fake(first_token_ms=0, delta_ms=0, deltas=10)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == 'agent.started' and 'route.selected' in kinds and 'retrieval.completed' in kinds and kinds[-1] == 'agent.completed'
    assert kinds.count('usage.recorded') == 4  # router, knowledge, knowledge, router: one per model turn
    assert {data['agent_id'] for kind, data in events if kind == 'response.delta'} == {'router'}  # only the root streams to the user
    cited, invalid = orchestrator.evidence.resolve(answer)
    assert [c['evidence_id'] for c in cited] == [1] and invalid == []


async def test_fake_usage_and_cost_are_unknown_not_zero():
    _, events, _ = await run_fake(first_token_ms=0, delta_ms=0)
    for _, data in (e for e in events if e[0] == 'usage.recorded'):
        assert data['usage'] is None and data['cost_usd'] is None


async def test_fake_latency_is_configurable():
    import time
    started = time.monotonic()
    await run_fake(first_token_ms=50, delta_ms=0, deltas=1)
    assert time.monotonic() - started >= 0.19  # four model turns of 50 ms


async def test_default_runtime_uses_the_fake_model_without_any_provider_key():
    runtime = default_runtime(Settings(model_provider='fake', _env_file=None), db=None)
    assert isinstance(runtime.gateway, FakeModel) and runtime.web_search is None
    await runtime.close()
