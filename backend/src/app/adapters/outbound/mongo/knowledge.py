"""Knowledge sources and chunks. One document per chunk; sources carry the state of the last valid index."""
import asyncio
from datetime import datetime, timezone
from pymongo import ReplaceOne
from app.bootstrap.settings import get_settings

SOURCE_FIELDS = ('document_type', 'title', 'source_url', 'publisher', 'region', 'language', 'tags', 'limitations')
HIDDEN = {'_id': 0, 'embedding': 0}
LEGACY_INDEX = 'workspace_id_1_source_id_1'  # one-document-per-source layout, replaced by per-chunk ids


def now():
    return datetime.now(timezone.utc)


class MongoKnowledgeRepository:
    def __init__(self, db, embedding_model: str | None = None):
        self.db, self.embedding_model = db, embedding_model or get_settings().embedding_model

    async def ensure_indexes(self):
        chunks = self.db.knowledge_chunks
        legacy = (await chunks.index_information()).get(LEGACY_INDEX)
        if legacy and legacy.get('unique'):  # old layout cannot be served; re-run the ingestion to rebuild it
            await chunks.drop_index(LEGACY_INDEX)
            await chunks.delete_many({'chunk_id': {'$exists': False}})
        await chunks.create_index([('workspace_id', 1), ('chunk_id', 1)], unique=True)
        await chunks.create_index([('workspace_id', 1), ('source_id', 1), ('chunk_index', 1)])
        await self.db.knowledge_sources.create_index([('workspace_id', 1), ('source_id', 1)], unique=True)

    # --- sources -------------------------------------------------------------------------------------------

    async def register_sources(self, workspace_id, sources):
        for source in sources:
            fields = {k: source.get(k) for k in SOURCE_FIELDS}
            await self.db.knowledge_sources.update_one(
                {'workspace_id': workspace_id, 'source_id': source['id']},
                {'$set': fields, '$setOnInsert': {'status': 'pending', 'created_at': now()}}, upsert=True)

    async def source_ids(self, workspace_id):
        cursor = self.db.knowledge_sources.find({'workspace_id': workspace_id}, {'source_id': 1}).sort('source_id', 1)
        return [doc['source_id'] for doc in await cursor.to_list(length=1000)]

    async def list_sources(self, workspace_id):
        cursor = self.db.knowledge_sources.find({'workspace_id': workspace_id}, {'_id': 0, 'versions': 0}).sort('source_id', 1)
        return await cursor.to_list(length=1000)

    async def get_source(self, workspace_id, source_id):
        return await self.db.knowledge_sources.find_one({'workspace_id': workspace_id, 'source_id': source_id}, {'_id': 0})

    async def list_chunks(self, workspace_id, source_id, limit=500):
        cursor = self.db.knowledge_chunks.find({'workspace_id': workspace_id, 'source_id': source_id}, HIDDEN)
        return await cursor.sort('chunk_index', 1).to_list(length=limit)

    async def is_current(self, workspace_id, source_id, index_version, embedding_model):
        """True when this exact content, processing and metadata version is already indexed with this model."""
        return bool(await self.db.knowledge_sources.find_one({
            'workspace_id': workspace_id, 'source_id': source_id,
            'index_version': index_version, 'embedding_model': embedding_model}, {'_id': 1}))

    async def replace_source(self, workspace_id, source_id, record, chunks, job_id=None):
        """Swap the source's chunks and state in one transaction, so readers and failures never see a mix."""
        at = now()
        key = {'workspace_id': workspace_id, 'source_id': source_id}
        docs = [{**c, 'workspace_id': workspace_id, 'indexed_at': at} for c in chunks]
        version = {k: record[k] for k in ('index_version', 'content_sha256', 'embedding_model', 'chunk_count', 'retrieved_on')}

        async def swap(session):
            if docs:
                await self.db.knowledge_chunks.bulk_write(
                    [ReplaceOne({'workspace_id': workspace_id, 'chunk_id': d['chunk_id']}, d, upsert=True) for d in docs],
                    session=session)
            await self.db.knowledge_chunks.delete_many(
                {**key, 'chunk_id': {'$nin': [d['chunk_id'] for d in docs]}}, session=session)
            await self.db.knowledge_sources.update_one(key, {
                '$set': {**record, 'status': 'indexed', 'last_indexed_at': at,
                         'last_attempt': {'at': at, 'outcome': 'updated', 'job_id': job_id}},
                '$push': {'versions': {'$each': [{**version, 'indexed_at': at}], '$slice': -10}}},
                upsert=True, session=session)

        async with self.db.client.start_session() as session:
            await session.with_transaction(swap)

    async def record_unchanged(self, workspace_id, source_id, job_id=None):
        at = now()
        await self.db.knowledge_sources.update_one(
            {'workspace_id': workspace_id, 'source_id': source_id},
            {'$set': {'status': 'indexed', 'last_attempt': {'at': at, 'outcome': 'already_current', 'job_id': job_id}}})

    async def record_failure(self, workspace_id, source_id, job_id, error):
        """Keeps the last valid version: `stale` if one exists, `failed` if the source never indexed."""
        await self.db.knowledge_sources.update_one(
            {'workspace_id': workspace_id, 'source_id': source_id},
            [{'$set': {'status': {'$cond': [{'$ifNull': ['$index_version', False]}, 'stale', 'failed']},
                       'last_attempt': {'at': now(), 'outcome': 'failed', 'job_id': job_id, 'error': error}}}])

    # --- retrieval -----------------------------------------------------------------------------------------

    async def hybrid_search(self, workspace_id, query, embedding, limit=5):
        """Vector + lexical search, both scoped to the workspace and the active embedding model, fused by RRF."""
        fetch = max(limit * 2, 10)
        eligible = {'workspace_id': workspace_id, 'embedding_model': self.embedding_model}
        vector_pipeline = [
            {'$vectorSearch': {'index': 'knowledge_vector', 'path': 'embedding', 'queryVector': embedding,
                               'numCandidates': max(100, fetch * 10), 'limit': fetch,
                               'filter': {'$and': [{k: {'$eq': v}} for k, v in eligible.items()]}}},
            {'$project': HIDDEN},
        ]
        lexical_pipeline = [
            {'$search': {'index': 'knowledge_text', 'compound': {
                'must': [{'text': {'query': query, 'path': ['title', 'heading', 'text']}}],
                'filter': [{'equals': {'path': k, 'value': v}} for k, v in eligible.items()]}}},
            {'$limit': fetch}, {'$project': HIDDEN},
        ]

        async def run(pipeline):
            return await (await self.db.knowledge_chunks.aggregate(pipeline)).to_list(length=fetch)

        return rrf_fuse(await asyncio.gather(run(vector_pipeline), run(lexical_pipeline)), limit)


def rrf_fuse(result_lists, limit, k=60):
    """Reciprocal rank fusion keyed by chunk_id; `score` is a rank score, not a probability of relevance."""
    scored = {}
    for results in result_lists:
        for position, doc in enumerate(results, start=1):
            entry = scored.setdefault(doc['chunk_id'], [0.0, doc])
            entry[0] += 1.0 / (k + position)
    ranked = sorted(scored.values(), key=lambda item: item[0], reverse=True)[:limit]
    return [{**doc, 'score': score} for score, doc in ranked]
