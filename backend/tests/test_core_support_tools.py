from datetime import date
from decimal import Decimal
import pytest
from app.application.use_cases.support import SupportService
from app.application.use_cases.tools import make_tools
from app.domain.identity import Identity
from app.domain.support import Receivable, Terminal

DEMO = Identity('cliente1988', 'demo', 'admin', is_demo=True)


class Repository:
    def __init__(self): self.asked = []
    async def get_receivables(self, identity):
        self.asked.append(identity)
        if (identity.workspace_id, identity.user_id) != ('demo', 'cliente1988'): return []
        return [Receivable(date(2026, 11, 2), date(2026, 11, 5), Decimal('3482.90'), 'previsto', 'credito_a_vista')]
    async def get_terminals(self, identity):
        self.asked.append(identity)
        if (identity.workspace_id, identity.user_id) != ('demo', 'cliente1988'): return []
        return [Terminal('Get Smart', 'sem conexão', 'Sem sinal da rede móvel', '4821', 'Algar', False)]


def tools(repo=None):
    return make_tools(SupportService(repo or Repository()), None, None, 'emb', None)


async def test_fixture_data_is_returned_with_demo_labelling_and_fixture_fields():
    result = await tools()['get_receivables'](DEMO, {})
    assert result['is_demo'] and 'demonstração' in result['notice'] and result['currency'] == 'BRL'
    assert result['items'][0]['amount'] == Decimal('3482.90') and result['items'][0]['payment_type'] == 'credito_a_vista'
    terminal = (await tools()['get_terminal_status'](DEMO, {}))['items'][0]
    assert (terminal['error'], terminal['serial_suffix'], terminal['carrier'], terminal['carrier_signal']) == ('Sem sinal da rede móvel', '4821', 'Algar', False)
    assert 'message' not in result


@pytest.mark.parametrize('name', ['get_receivables', 'get_terminal_status'])
async def test_absence_returns_a_message_instead_of_invented_data(name):
    result = await tools()[name](Identity('sem-dados', 'demo', 'operator'), {})
    assert result['items'] == [] and 'Informe a ausência' in result['message']


@pytest.mark.parametrize('name', ['get_receivables', 'get_terminal_status'])
async def test_model_supplied_customer_arguments_are_ignored(name):
    repo = Repository()
    result = await tools(repo)[name](Identity('sem-dados', 'demo', 'operator'), {'user_id': 'cliente1988', 'workspace_id': 'demo', 'customer': 'cliente1988'})
    assert result['items'] == []  # the other customer is not revealed
    assert [(i.user_id, i.workspace_id) for i in repo.asked] == [('sem-dados', 'demo')]


async def test_search_knowledge_exposes_chunk_provenance_and_rank_score():
    chunk = {'chunk_id': 'c1', 'source_id': 's1', 'title': 'Pix', 'heading': 'Prazo', 'text': 't', 'source_url': 'https://x.test',
             'retrieved_on': '2026-11-03', 'limitations': [], 'document_type': 'curated_summary', 'index_version': 'v2', 'score': 0.03,
             'embedding_model': 'interno'}

    class Knowledge:
        async def hybrid_search(self, workspace_id, query, embedding, limit=5): return [chunk]

    class Model:
        async def embed(self, *, model, texts): return [[0.1]]

    result = await make_tools(None, Knowledge(), Model(), 'emb', None)['search_knowledge'](DEMO, {'query': 'prazo pix'})
    item = result['results'][0]
    assert (item['chunk_id'], item['heading'], item['index_version'], item['score']) == ('c1', 'Prazo', 'v2', 0.03)
    assert 'embedding_model' not in item  # only whitelisted fields reach the model
