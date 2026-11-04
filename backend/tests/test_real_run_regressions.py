"""Regressions found in the first real run against the provider."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from app.adapters.outbound.openai.web import OpenAIWebSearch
from app.application.use_cases.guardrails import build_instructions


async def test_web_keeps_provider_data_source_without_url():
    weather = NS(type='web_search_call', action=NS(sources=[NS(type='api', name='oai-weather', url=None)]))
    response = NS(output=[weather], output_text='Amanhã: 19 a 23 °C.')

    class Responses:
        async def create(self, **kwargs): return response

    result = await OpenAIWebSearch(NS(responses=Responses()), 'mock')('clima amanhã')
    assert result['status'] == 'ok'
    assert result['sources'] == [{'url': None, 'title': 'Serviço de dados do provedor de busca (oai-weather)'}]


def test_instructions_carry_local_and_utc_date():
    now = datetime(2026, 11, 3, 23, 5, tzinfo=timezone(timedelta(hours=-3)))
    text = build_instructions('Base.', [], (), now=now)
    assert '03/11/2026 23:05 (America/Sao_Paulo), 04/11/2026 02:05 UTC' in text


async def test_router_receives_conversation_history():
    from app.application.use_cases.orchestrator import Orchestrator
    from app.domain.identity import Identity
    from app.domain.models import Agent, AgentKind, ModelEvent

    seen = []

    class Model:
        async def stream(self, *, model, instructions, inputs, tools, **kwargs):
            seen.append(list(inputs))
            yield ModelEvent('text_delta', text='Pelo procedimento do chip Algar [1].')
            yield ModelEvent('completed', data={'output': [], 'usage': None})

    async def emit(*args): pass
    history = [{'role': 'user', 'content': 'Minha maquininha está sem internet.'},
               {'role': 'assistant', 'content': 'Reinsira o chip Algar e faça o teste de comunicação.'}]
    router = Agent('router', 'Roteador', 'r', 'Regra', AgentKind.ROUTER, 'mock')
    await Orchestrator(Model(), {'router': router}, {}).run('Como você sabe disso?', Identity('u', 'w', 'admin'), emit, history)
    assert seen[0] == [*history, {'role': 'user', 'content': 'Como você sabe disso?'}]


def test_extractor_ignores_a_small_article_card_when_main_is_missing():
    from app.adapters.outbound.web.extract import extract_markdown
    page = ('<html><body><header>Menu do site</header><article><p>Teaser curto</p></article>'
            '<section><h1>Link de Pagamento</h1><p>' + 'Venda pelo WhatsApp com segurança antifraude. ' * 20 + '</p></section></body></html>')
    text = extract_markdown(page)
    assert '# Link de Pagamento' in text and 'WhatsApp' in text and 'Menu do site' not in text


def test_extractor_keeps_an_article_that_holds_the_page_content():
    from app.adapters.outbound.web.extract import extract_markdown
    page = ('<html><body><div>Links rápidos</div><article><h1>Antecipação</h1><p>' + 'Solicite pelo aplicativo. ' * 20 +
            '</p></article></body></html>')
    text = extract_markdown(page)
    assert text.startswith('# Antecipação') and 'Links rápidos' not in text
