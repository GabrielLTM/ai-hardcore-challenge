"""HTTP contract of the agent administration endpoints (permissions, ETag/If-Match, 409/412)."""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.adapters.inbound.http.agents_routes import current_identity, router
from app.adapters.inbound.http.errors import ApiError
from app.adapters.outbound.mongo.seed import seed_demo
from app.bootstrap.settings import get_settings
from app.domain.identity import Identity
from test_agents_mongo import db  # noqa: F401  (fixture: throwaway Mongo database)


@pytest.fixture
async def api(db, monkeypatch):  # noqa: F811
    monkeypatch.setenv('MONGO_DATABASE', db.name)
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(router)
    app.state.mongo = db.client

    @app.exception_handler(ApiError)
    async def render(request, exc: ApiError):  # the real handler belongs to the app composition root
        body = {'code': exc.code, 'message': exc.message, 'request_id': 'test'}
        return JSONResponse({**body, **({'details': exc.details} if exc.details else {})}, status_code=exc.status)
    await seed_demo(db, get_settings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        client.app = app
        yield client
    get_settings.cache_clear()


ROUTES = [('GET', '/agents'), ('POST', '/agents'), ('GET', '/agents/router'), ('PATCH', '/agents/router'),
          ('GET', '/agents/router/relationships'), ('POST', '/agents/router/relationships/validate'),
          ('POST', '/agents/router/publish'), ('GET', '/agents/router/versions'), ('GET', '/agents/router/versions/1'),
          ('GET', '/agents/router/versions/1/diff/1'), ('POST', '/agents/router/versions/1/restore'),
          ('GET', '/prompts/prompt-router/versions'), ('GET', '/graph/versions')]


@pytest.mark.parametrize('method,path', ROUTES)
async def test_every_route_is_admin_only(api, method, path):
    api.app.dependency_overrides[current_identity] = lambda: Identity('op1', 'demo', 'operator')
    response = await api.request(method, f'/api/v1{path}', json={} if method != 'GET' else None)
    assert response.status_code == 403 and response.json()['code'] == 'forbidden'


async def test_create_edit_with_etag_and_stale_edit_is_412(api):
    created = await api.post('/api/v1/agents', json={'name': 'Cobrança Pix', 'instructions': 'Ajude com Pix.', 'tools': ['search_knowledge']})
    body = created.json()
    assert created.status_code == 201 and created.headers['etag'] == '"1"'
    assert body['id'].startswith('cobranca-pix-') and body['status'] == 'draft' and body['model'] == 'gpt-6-luna' and body['version'] is None
    url = f"/api/v1/agents/{body['id']}"
    assert (await api.patch(url, json={'description': 'x'})).status_code == 428
    assert (await api.patch(url, json={'description': 'x'}, headers={'If-Match': 'abc'})).json()['code'] == 'invalid_if_match'
    saved = await api.patch(url, json={'description': 'Atende Pix', 'limits': {'max_output_tokens': 500}}, headers={'If-Match': '"1"'})
    assert saved.status_code == 200 and saved.headers['etag'] == '"2"' and saved.json()['limits'] == {'max_output_tokens': 500}
    stale = await api.patch(url, json={'description': 'minha edição'}, headers={'If-Match': '"1"'})
    assert stale.status_code == 412 and stale.json()['code'] == 'revision_conflict' and stale.json()['details'] == {'current_revision': 2}
    assert (await api.get(url)).json()['description'] == 'Atende Pix'
    assert (await api.patch(url, json={'revision': 9}, headers={'If-Match': '2'})).status_code == 422  # unknown fields are rejected
    assert (await api.patch(url, json={'api_key': 'x'}, headers={'If-Match': '2'})).status_code == 422
    secret = await api.patch(url, json={'instructions': 'chave sk-abcdefghijklmnopqrstuvwxyz123456'}, headers={'If-Match': '2'})
    assert secret.status_code == 422 and secret.json()['code'] == 'secret_detected'


async def test_publish_cycle_is_409_and_valid_publish_then_history_diff_restore(api):
    knowledge = (await api.get('/api/v1/agents/knowledge')).json()
    await api.patch('/api/v1/agents/knowledge', json={'delegates': ['router'], 'tools': ['delegate_to_agent']}, headers={'If-Match': str(knowledge['revision'])})
    checked = await api.post('/api/v1/agents/knowledge/relationships/validate', json={'delegates': ['router'], 'tools': ['delegate_to_agent']})
    assert checked.status_code == 200 and checked.json()['valid'] is False and checked.json()['cycle'][0] == 'knowledge'
    refused = await api.post('/api/v1/agents/knowledge/publish')
    assert refused.status_code == 409 and refused.json()['code'] == 'graph_invalid' and refused.json()['details']['cycle']
    assert (await api.get('/api/v1/agents/knowledge/versions')).json()['items'][0]['version'] == 1  # previous version stays active

    await api.patch('/api/v1/agents/knowledge', json={'delegates': [], 'tools': ['search_knowledge', 'web_search'], 'instructions': 'Nova instrução.'},
                    headers={'If-Match': '2'})
    stale_publish = await api.post('/api/v1/agents/knowledge/publish', headers={'If-Match': '1'})
    assert stale_publish.status_code == 412
    published = await api.post('/api/v1/agents/knowledge/publish', json={'summary': 'Ajuste de instrução'}, headers={'If-Match': '3'})
    assert published.status_code == 201 and published.json()['version'] == 2 and published.json()['graph_version'] == '2'
    assert (await api.post('/api/v1/agents/knowledge/publish')).json()['code'] == 'no_changes'

    versions = (await api.get('/api/v1/agents/knowledge/versions')).json()
    assert [(v['version'], v['summary']) for v in versions['items']] == [(2, 'Ajuste de instrução'), (1, 'Versão inicial (seed)')]
    diff = (await api.get('/api/v1/agents/knowledge/versions/1/diff/2')).json()
    assert [c['field'] for c in diff['changes']] == ['instructions'] and any(l['op'] == 'add' for l in diff['changes'][0]['lines'])
    restored = await api.post('/api/v1/agents/knowledge/versions/1/restore')
    assert restored.status_code == 201 and restored.json()['version'] == 3
    assert (await api.get('/api/v1/agents/knowledge/versions/3')).json()['restored_from'] == 1
    assert [p['version'] for p in (await api.get('/api/v1/prompts/prompt-knowledge/versions')).json()['items']] == [3, 2, 1]
    assert [g['id'] for g in (await api.get('/api/v1/graph/versions')).json()['items']] == ['3', '2', '1']
    assert (await api.get('/api/v1/agents/knowledge/versions/9')).json()['code'] == 'version_not_found'
    assert (await api.get('/api/v1/agents/knowledge/versions/1/diff/9')).status_code == 404


async def test_relationships_list_filters_pagination_and_not_found(api):
    published = (await api.get('/api/v1/agents/knowledge/relationships')).json()
    assert published['state'] == 'published' and sorted(e['id'] for e in published['inbound']) == ['router', 'support']
    assert (await api.get('/api/v1/agents/knowledge/relationships?state=draft')).status_code == 200
    assert (await api.get('/api/v1/agents/knowledge/relationships?state=other')).status_code == 422
    first = (await api.get('/api/v1/agents?limit=2')).json()
    assert [a['id'] for a in first['items']] == ['router', 'support'] and 'instructions' not in first['items'][0]
    second = (await api.get('/api/v1/agents', params={'limit': 2, 'cursor': first['next_cursor']})).json()
    assert [a['id'] for a in second['items']] == ['knowledge', 'escalation'] and second['next_cursor'] is None
    assert (await api.get('/api/v1/agents?kind=support&status=published&q=atendimento')).json()['items'][0]['id'] == 'support'
    assert (await api.get('/api/v1/agents?cursor=%21%21')).json()['code'] == 'invalid_cursor'
    assert (await api.get('/api/v1/agents/nao-existe')).status_code == 404
    assert (await api.get('/api/v1/prompts/nao-existe/versions')).json()['code'] == 'prompt_not_found'
    assert (await api.post('/api/v1/agents', json={'name': '', 'instructions': 'x'})).status_code == 422
