import asyncio
from types import SimpleNamespace as NS
import pytest
from app.adapters.outbound.mongo.knowledge import MongoKnowledgeRepository, rrf_fuse
from app.adapters.outbound.openai.web import OpenAIWebSearch


def chunk(chunk_id, source_id='s'):
    return {'chunk_id': chunk_id, 'source_id': source_id, 'title': 'T', 'text': chunk_id, 'source_url': 'https://x/',
            'retrieved_on': '2026-11-03', 'limitations': [], 'document_type': 'official_page', 'index_version': 'v'}


class Cursor:
    def __init__(self, docs): self.docs = docs
    async def to_list(self, length): return self.docs[:length]


class Collection:
    def __init__(self, vector, lexical):
        self.vector, self.lexical, self.pipelines = vector, lexical, []

    async def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        await asyncio.sleep(0)
        return Cursor(self.vector if '$vectorSearch' in pipeline[0] else self.lexical)


def repository(vector, lexical, model='embedding'):
    collection = Collection(vector, lexical)
    return MongoKnowledgeRepository(NS(knowledge_chunks=collection), model), collection


@pytest.mark.asyncio
async def test_hybrid_search_fuses_both_lists_by_reciprocal_rank():
    repo, _ = repository([chunk('a#0'), chunk('b#0'), chunk('c#0')], [chunk('c#0'), chunk('d#0'), chunk('a#0')])
    results = await repo.hybrid_search('ws', 'maquininha get smart', [0.1, 0.2], limit=3)
    assert [r['chunk_id'] for r in results] == ['a#0', 'c#0', 'b#0']  # a: 1st+3rd, c: 3rd+1st (tie keeps vector order), b: 2nd only
    assert results[0]['score'] == pytest.approx(1 / 61 + 1 / 63)
    contract = {'chunk_id', 'source_id', 'title', 'text', 'source_url', 'retrieved_on', 'limitations', 'document_type', 'index_version'}
    assert all(contract <= r.keys() for r in results)


@pytest.mark.asyncio
async def test_both_searches_filter_by_workspace_and_embedding_model():
    repo, collection = repository([], [], model='m-1')
    await repo.hybrid_search('ws-9', 'pix', [0.5], limit=2)
    vector = next(p for p in collection.pipelines if '$vectorSearch' in p[0])[0]['$vectorSearch']
    lexical = next(p for p in collection.pipelines if '$search' in p[0])[0]['$search']
    assert vector['queryVector'] == [0.5] and vector['index'] == 'knowledge_vector'
    assert {'workspace_id': {'$eq': 'ws-9'}} in vector['filter']['$and'] and {'embedding_model': {'$eq': 'm-1'}} in vector['filter']['$and']
    assert lexical['index'] == 'knowledge_text' and lexical['compound']['must'][0]['text']['query'] == 'pix'
    assert {'equals': {'path': 'workspace_id', 'value': 'ws-9'}} in lexical['compound']['filter']
    assert {'equals': {'path': 'embedding_model', 'value': 'm-1'}} in lexical['compound']['filter']


def test_rrf_dedupes_by_chunk_not_by_source_and_respects_limit():
    fused = rrf_fuse([[chunk('a#0', 's'), chunk('a#1', 's')], [chunk('a#1', 's')]], limit=1)
    assert [d['chunk_id'] for d in fused] == ['a#1']
    assert rrf_fuse([[], []], 5) == []


# --- hosted web search -------------------------------------------------------------------------------

class Responses:
    def __init__(self, result=None, error=None, delay=0):
        self.result, self.error, self.delay, self.kwargs = result, error, delay, None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


def search(result=None, **kwargs):
    responses = Responses(result, **kwargs)
    return OpenAIWebSearch(NS(responses=responses), 'modelo', timeout_s=kwargs.get('timeout', 0.05)), responses


def answer(text='Faz 24°C em Porto Alegre.', urls=('https://clima.example/poa',), cited=True):
    searched = NS(type='web_search_call', action=NS(sources=[NS(url=u) for u in urls] + [NS(url=urls[0])]))
    notes = [NS(type='url_citation', url=urls[0], title='Clima POA')] if cited else []
    return NS(output_text=text, output=[searched, NS(type='message', content=[NS(annotations=notes)])])


@pytest.mark.asyncio
async def test_web_search_returns_answer_sources_and_retrieval_time():
    tool, responses = search(answer())
    result = await tool('clima em Porto Alegre')
    assert result['status'] == 'ok' and result['answer'].startswith('Faz 24')
    assert result['sources'] == [{'url': 'https://clima.example/poa', 'title': 'Clima POA'}]
    assert result['retrieved_at'].endswith('Z')
    assert responses.kwargs['tools'] == [{'type': 'web_search'}] and responses.kwargs['input'] == 'clima em Porto Alegre'


@pytest.mark.asyncio
async def test_web_search_never_invents_sources():
    tool, _ = search(answer(cited=False, urls=('https://a.example/',)))
    assert (await tool('x'))['sources'] == [{'url': 'https://a.example/'}]
    tool, _ = search(NS(output_text='Resposta sem fontes.', output=[]))
    assert (await tool('x')) | {'retrieved_at': None} == {'status': 'ok', 'answer': 'Resposta sem fontes.', 'sources': [], 'retrieved_at': None}


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [{'error': RuntimeError('Incorrect API key sk-secret')}, {'delay': 1.0},
                                    {'result': NS(output_text='', output=[])}])
async def test_web_search_reports_unavailability_instead_of_raising(kwargs):
    tool, _ = search(**kwargs)
    result = await tool('câmbio do dólar hoje')
    assert result['status'] == 'unavailable' and result['answer'] == '' and result['sources'] == []
    assert result['retrieved_at'] and 'secret' not in str(result)
