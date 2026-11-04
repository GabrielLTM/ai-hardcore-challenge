import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from app.adapters.inbound.http.errors import ApiError, register_error_handlers
from app.domain.errors import CapacityExceeded, IdempotencyConflict


class Body(BaseModel):
    message: str


def make_app():
    app = FastAPI()
    register_error_handlers(app)

    @app.get('/api')
    async def api(): raise ApiError(403, 'forbidden', 'Sem acesso.', {'x': 1})

    @app.get('/http')
    async def http(): raise HTTPException(409, 'conflito')

    @app.get('/boom')
    async def boom(): raise RuntimeError('detalhe interno secreto')

    @app.get('/domain')
    async def domain(): raise IdempotencyConflict()

    @app.get('/cap')
    async def cap(): raise CapacityExceeded()

    @app.post('/body')
    async def body(payload: Body): return payload
    return app


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(), raise_app_exceptions=False), base_url='http://t') as c:
        yield c


async def test_every_error_kind_has_the_structured_shape_and_request_id(client):
    cases = [(await client.get('/api'), 403, 'forbidden'), (await client.get('/http'), 409, 'conflict'),
             (await client.get('/missing'), 404, 'not_found'), (await client.get('/boom'), 500, 'internal_error'),
             (await client.post('/body', json={}), 422, 'validation_error'), (await client.get('/domain'), 409, 'idempotency_conflict'),
             (await client.get('/cap'), 429, 'capacity_exceeded')]
    for response, status, code in cases:
        body = response.json()
        assert response.status_code == status and body['code'] == code and body['message']
        assert body['request_id'] == response.headers['x-request-id'] and set(body) <= {'code', 'message', 'request_id', 'details'}
    assert cases[0][0].json()['details'] == {'x': 1}
    assert 'secreto' not in cases[3][0].text
    assert cases[6][0].headers['retry-after'] == '5'
    details = cases[4][0].json()['details']
    assert details[0]['loc'] == ['body', 'message'] and 'input' not in details[0]


async def test_request_id_is_honoured_when_sane_and_replaced_when_not(client):
    ok = await client.get('/api', headers={'X-Request-ID': 'abc-12345'})
    assert ok.headers['x-request-id'] == 'abc-12345' and ok.json()['request_id'] == 'abc-12345'
    bad = await client.get('/api', headers={'X-Request-ID': 'x y!'})
    assert bad.headers['x-request-id'] != 'x y!' and len(bad.headers['x-request-id']) == 32
