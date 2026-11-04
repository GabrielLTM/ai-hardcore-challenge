"""Shared doubles for knowledge tests: in-memory repository, fake embeddings, mocked official site."""
import httpx
from app.adapters.outbound.web.extract import extract_markdown
from app.adapters.outbound.web.fetch import SafeFetcher
from app.application.use_cases.ingest_sources import IngestionPipeline
from app.domain.knowledge import OFFICIAL

PUBLIC_IP = '93.184.216.34'
HOSTS = ['site.getnet.com.br']


class FakeGateway:
    def __init__(self, dimensions=2, error=None):
        self.calls, self.texts, self.dimensions, self.error = 0, [], dimensions, error

    async def embed(self, *, model, texts):
        self.calls += 1
        self.texts += texts
        if self.error:
            raise self.error
        return [[float(len(t) % 7)] * self.dimensions for t in texts]


class MemoryRepository:
    """Same contract as MongoKnowledgeRepository for the ingestion pipeline."""

    def __init__(self):
        self.sources, self.chunks, self.failures, self.writes = {}, {}, [], 0

    async def register_sources(self, workspace_id, sources):
        for source in sources:
            self.sources.setdefault((workspace_id, source['id']), {'status': 'pending'})

    async def is_current(self, workspace_id, source_id, index_version, embedding_model):
        source = self.sources.get((workspace_id, source_id), {})
        return source.get('index_version') == index_version and source.get('embedding_model') == embedding_model

    async def replace_source(self, workspace_id, source_id, record, chunks, job_id=None):
        self.writes += 1
        self.sources[(workspace_id, source_id)] = {**record, 'status': 'indexed'}
        self.chunks[(workspace_id, source_id)] = {c['chunk_id']: c for c in chunks}

    async def record_unchanged(self, workspace_id, source_id, job_id=None):
        self.sources[(workspace_id, source_id)]['status'] = 'indexed'

    async def record_failure(self, workspace_id, source_id, job_id, error):
        source = self.sources.setdefault((workspace_id, source_id), {})
        source['status'] = 'stale' if source.get('index_version') else 'failed'
        self.failures.append((source_id, error))


def official(source_id='official-x', url='https://site.getnet.com.br/pagina/'):
    return {'id': source_id, 'title': 'Página X', 'source_url': url, 'publisher': 'Getnet', 'region': 'BR',
            'language': 'pt-BR', 'tags': ['x'], 'limitations': ['Limite editorial.'], 'document_type': OFFICIAL}


def page(*sections, chrome=True):
    """HTML with navigation chrome around sections of (heading, paragraph) pairs."""
    body = ''.join(f'<h2>{h}</h2><p>{p}</p>' for h, p in sections)
    nav = '<header><nav>Menu Sou cliente</nav></header>' if chrome else ''
    foot = '<footer>Rodapé institucional</footer>' if chrome else ''
    return f'<html><head><title>t</title><style>x{{}}</style></head><body>{nav}<main><h1>Página X</h1>{body}</main>{foot}<script>x()</script></body></html>'


LONG = 'Texto explicativo sobre o produto e como utilizá-lo no dia a dia do comércio. ' * 4


def site(handler_or_html):
    """SafeFetcher over a mock transport; `handler_or_html` is HTML text or a callable(request) -> Response."""
    if isinstance(handler_or_html, str):
        html = handler_or_html
        handler_or_html = lambda request: httpx.Response(200, headers={'content-type': 'text/html; charset=utf-8'}, text=html)

    async def resolver(host):
        return [PUBLIC_IP]

    return SafeFetcher(HOSTS, transport=httpx.MockTransport(handler_or_html), resolver=resolver)


def pipeline(repository, fetcher=None, gateway='default', **kwargs):
    gateway = FakeGateway() if gateway == 'default' else gateway
    return IngestionPipeline(repository=repository, gateway=gateway, fetcher=fetcher, extract_html=extract_markdown,
                             embedding_model='embedding', curated_root=kwargs.pop('curated_root', None),
                             dimensions=kwargs.pop('dimensions', 2), **kwargs)
