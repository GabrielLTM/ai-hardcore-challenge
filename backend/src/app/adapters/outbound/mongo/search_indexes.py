"""Atlas Search (`knowledge_text`) and Vector Search (`knowledge_vector`) indexes; creation is idempotent."""
import asyncio
import time
from pymongo.operations import SearchIndexModel

TEXT_INDEX, VECTOR_INDEX, COLLECTION = 'knowledge_text', 'knowledge_vector', 'knowledge_chunks'


def search_index_models(dimensions: int) -> list[SearchIndexModel]:
    portuguese = {'type': 'string', 'analyzer': 'lucene.portuguese'}
    return [
        SearchIndexModel(name=TEXT_INDEX, type='search', definition={'mappings': {'dynamic': False, 'fields': {
            'title': portuguese, 'heading': portuguese, 'text': portuguese,
            'workspace_id': {'type': 'token'}, 'embedding_model': {'type': 'token'}}}}),
        SearchIndexModel(name=VECTOR_INDEX, type='vectorSearch', definition={'fields': [
            {'type': 'vector', 'path': 'embedding', 'numDimensions': dimensions, 'similarity': 'cosine'},
            {'type': 'filter', 'path': 'workspace_id'}, {'type': 'filter', 'path': 'embedding_model'}]}),
    ]


async def _existing(db) -> dict[str, dict]:
    return {index['name']: index async for index in await db[COLLECTION].list_search_indexes()}


async def ensure_search_indexes(db, dimensions: int) -> dict[str, str]:
    """Create missing indexes; refuse (instead of silently mixing) when the vector dimension changed."""
    if COLLECTION not in await db.list_collection_names():
        await db.create_collection(COLLECTION)
    existing, status = await _existing(db), {}
    for model in search_index_models(dimensions):
        name = model.document['name']
        if name not in existing:
            await db[COLLECTION].create_search_index(model)
            status[name] = 'created'
            continue
        if name == VECTOR_INDEX:
            current = existing[name].get('latestDefinition', {}).get('fields', [{}])[0].get('numDimensions')
            if current != dimensions:
                raise RuntimeError(f'O índice {name} usa {current} dimensões e o modelo configurado usa {dimensions}. '
                                   'Remova o índice e reprocesse a base para trocar de modelo.')
        status[name] = 'exists'
    return status


async def wait_queryable(db, timeout: float = 120.0) -> bool:
    """Atlas Local builds indexes asynchronously; poll until both are queryable or the timeout passes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        existing = await _existing(db)
        if all(existing.get(n, {}).get('queryable') for n in (TEXT_INDEX, VECTOR_INDEX)):
            return True
        await asyncio.sleep(1)
    return False
