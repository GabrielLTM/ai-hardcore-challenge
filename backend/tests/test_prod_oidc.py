"""Production identity: OIDC bearer tokens against a JWKS served by a simulated issuer (RSA keys generated here)."""
import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from app.adapters.inbound.http import agents_routes, knowledge_routes, routes
from app.adapters.inbound.http.errors import ApiError, register_error_handlers
from app.adapters.inbound.http.oidc import OidcVerifier
from app.bootstrap.settings import get_settings
from app.domain.identity import Identity

ISSUER, AUDIENCE = 'https://issuer.test', 'getnet-atendimento'


def new_key(kid: str):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    return SimpleNamespace(private=private, kid=kid, jwk={**jwk, 'kid': kid, 'use': 'sig', 'alg': 'RS256'})


KEY, OTHER = new_key('k1'), new_key('k1')  # OTHER reuses the kid: a forger who copies the header


def claims(**over):
    now = int(time.time())
    base = {'iss': ISSUER, 'aud': AUDIENCE, 'sub': 'user-1', 'iat': now, 'exp': now + 300, 'workspace_id': 'ws1', 'roles': ['operator']}
    return {k: v for k, v in {**base, **over}.items() if v is not None}


def token(payload=None, key=KEY, **headers):
    return jwt.encode(payload if payload is not None else claims(), key.private, algorithm='RS256', headers={'kid': key.kid, **headers})


class Issuer:
    """Simulated identity provider: discovery + JWKS, with request counters and an outage switch."""

    def __init__(self, *keys):
        self.keys, self.requests, self.down = list(keys), [], False
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if self.down: return httpx.Response(503)
        if request.url.path == '/.well-known/openid-configuration':
            return httpx.Response(200, json={'issuer': ISSUER, 'jwks_uri': f'{ISSUER}/jwks'})
        if request.url.path == '/jwks':
            return httpx.Response(200, json={'keys': [k.jwk for k in self.keys]})
        return httpx.Response(404)

    def verifier(self, **options):
        return OidcVerifier(self.client, issuer=ISSUER, audience=AUDIENCE, **options)


@pytest.fixture
def issuer():
    return Issuer(KEY)


async def rejected(verifier, raw, status=401):
    with pytest.raises(ApiError) as exc:
        await verifier.authenticate(raw)
    assert exc.value.status == status
    return exc.value


async def test_valid_token_maps_sub_workspace_and_role(issuer):
    verifier = issuer.verifier()
    assert await verifier.authenticate(token()) == Identity('user-1', 'ws1', 'operator')
    assert (await verifier.authenticate(token(claims(roles=['operator', 'admin'])))).role == 'admin'


async def test_signature_from_another_key_is_rejected_even_with_the_same_kid(issuer):
    error = await rejected(issuer.verifier(), token(key=OTHER))
    assert error.code == 'unauthorized' and error.headers['WWW-Authenticate'] == 'Bearer error="invalid_token"'


async def test_expired_token_is_401_with_leeway_for_clock_skew(issuer):
    verifier = issuer.verifier(leeway=30)
    assert (await verifier.authenticate(token(claims(exp=int(time.time()) - 10)))).user_id == 'user-1'  # inside the tolerance
    error = await rejected(verifier, token(claims(exp=int(time.time()) - 120)))
    assert 'expirada' in error.message and error.headers['WWW-Authenticate'].startswith('Bearer')


async def test_not_yet_valid_token_is_rejected(issuer):
    await rejected(issuer.verifier(leeway=30), token(claims(nbf=int(time.time()) + 600)))


@pytest.mark.parametrize('payload', [claims(aud='outra-api'), claims(iss='https://evil.test'), claims(sub=None), claims(exp=None),
                                     claims(aud=None), claims(sub='')])
async def test_audience_issuer_and_required_claims_are_enforced(issuer, payload):
    await rejected(issuer.verifier(), token(payload))


def forged(header: dict, payload: dict, signature: bytes = b'') -> str:
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
    return f'{b64(json.dumps(header).encode())}.{b64(json.dumps(payload).encode())}.{b64(signature)}'


async def test_alg_none_is_never_accepted(issuer):
    await rejected(issuer.verifier(), forged({'alg': 'none', 'typ': 'JWT', 'kid': 'k1'}, claims()))
    await rejected(issuer.verifier(), jwt.encode(claims(), None, algorithm='none', headers={'kid': 'k1'}))


async def test_hmac_signed_with_the_public_key_is_rejected(issuer):
    public_pem = KEY.private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    header = {'alg': 'HS256', 'typ': 'JWT', 'kid': 'k1'}
    unsigned = forged(header, claims()).rsplit('.', 1)[0]
    signature = hmac.new(public_pem, unsigned.encode(), hashlib.sha256).digest()
    await rejected(issuer.verifier(), forged(header, claims(), signature))


@pytest.mark.parametrize('raw', [None, '', 'abc', 'a.b.c', 'x' * 5000])
async def test_missing_or_malformed_token_is_401_with_challenge(issuer, raw):
    error = await rejected(issuer.verifier(), raw)
    assert error.headers['WWW-Authenticate'].startswith('Bearer')


async def test_missing_token_has_plain_challenge(issuer):
    assert (await rejected(issuer.verifier(), None)).headers['WWW-Authenticate'] == 'Bearer'


async def test_token_with_a_non_string_kid_is_rejected(issuer):
    for kid in (['k1'], {'a': 1}, 7):  # PyJWT refuses to encode these, so the token is forged by hand
        await rejected(issuer.verifier(), forged({'alg': 'RS256', 'typ': 'JWT', 'kid': kid}, claims(), b'sig'))


async def test_role_and_workspace_mapping_is_403_not_401(issuer):
    verifier = issuer.verifier(allowed_workspaces=('ws1', 'ws2'))
    for payload in (claims(roles=[]), claims(roles=['viewer']), claims(roles=None), claims(roles={'admin': True})):
        assert (await rejected(verifier, token(payload), 403)).code == 'forbidden'
    for workspace in (None, 'ws9', 'ws1/../x', 'a' * 65, 7):
        assert (await rejected(verifier, token(claims(workspace_id=workspace)), 403)).code == 'forbidden'


async def test_claim_names_and_nested_roles_are_configurable(issuer):
    verifier = issuer.verifier(workspace_claim='https://getnet.test/tenant', roles_claim='realm_access.roles', admin_role='atendimento-admin')
    payload = claims(roles=None, realm_access={'roles': ['x', 'atendimento-admin']}, **{'https://getnet.test/tenant': 'ws7'})
    assert await verifier.authenticate(token(payload)) == Identity('user-1', 'ws7', 'admin')
    assert (await issuer.verifier().authenticate(token(claims(roles='operator')))).role == 'operator'  # a single string is accepted too


async def test_jwks_is_cached_and_rotation_is_picked_up_with_a_refetch_floor(issuer):
    now = [1000.0]
    verifier = issuer.verifier(jwks_ttl=600, min_refresh=30, clock=lambda: now[0])
    for _ in range(5): await verifier.authenticate(token())
    assert issuer.requests == ['/.well-known/openid-configuration', '/jwks']  # one discovery + one JWKS for five requests

    rotated = new_key('k2')
    issuer.keys = [rotated]  # the issuer rotates: k1 is gone, k2 is new
    await rejected(verifier, token(key=rotated))  # unknown kid but refetched less than 30s ago: no new request
    assert len(issuer.requests) == 2
    now[0] += 31
    assert (await verifier.authenticate(token(key=rotated))).user_id == 'user-1'  # unknown kid after the floor: refetch finds k2
    assert len(issuer.requests) == 4
    for _ in range(20): await rejected(verifier, token(key=new_key('nope')))  # a flood of random kids cannot hammer the issuer
    assert len(issuer.requests) == 4


async def test_issuer_outage_cold_is_503_and_warm_keeps_serving_known_keys(issuer):
    now = [0.0]
    verifier = issuer.verifier(jwks_ttl=100, min_refresh=10, clock=lambda: now[0])
    issuer.down = True
    error = await rejected(verifier, token(), 503)
    assert error.code == 'service_unavailable' and error.headers['Retry-After']
    issuer.down, now[0] = False, now[0] + 11
    await verifier.authenticate(token())
    issuer.down, now[0] = True, now[0] + 500  # cache is past its TTL and the issuer is unreachable
    assert (await verifier.authenticate(token())).user_id == 'user-1'


async def test_discovery_must_name_the_configured_issuer_and_https_jwks(issuer):
    def evil(request):
        if request.url.path.endswith('openid-configuration'):
            return httpx.Response(200, json={'issuer': 'https://evil.test', 'jwks_uri': f'{ISSUER}/jwks'})
        return httpx.Response(200, json={'keys': [KEY.jwk]})
    verifier = OidcVerifier(httpx.AsyncClient(transport=httpx.MockTransport(evil)), issuer=ISSUER, audience=AUDIENCE)
    await rejected(verifier, token(), 503)
    insecure = OidcVerifier(issuer.client, issuer=ISSUER, audience=AUDIENCE, jwks_uri='http://issuer.test/jwks')
    await rejected(insecure, token(), 503)


async def test_encryption_keys_in_the_jwks_are_ignored():
    enc = new_key('enc')
    issuer = Issuer(KEY)
    issuer.keys.append(SimpleNamespace(jwk={**enc.jwk, 'use': 'enc'}))
    await issuer.verifier().authenticate(token())
    await rejected(issuer.verifier(), token(key=enc))


def test_production_profile_refuses_unsafe_configuration():
    from app.bootstrap.settings import Settings
    common = dict(app_profile='production', oidc_issuer=ISSUER, oidc_audience=AUDIENCE, _env_file=None)
    with pytest.raises(ValueError, match='OIDC_ISSUER e OIDC_AUDIENCE'):
        Settings(app_profile='production', _env_file=None)
    with pytest.raises(ValueError, match='https'):
        Settings(**{**common, 'oidc_issuer': 'http://issuer.test'})
    with pytest.raises(ValueError, match='fake'):
        Settings(**common, model_provider='fake')
    for algorithms in ('HS256', 'none', 'RS256,HS256', ''):
        with pytest.raises(ValueError, match='OIDC_ALGORITHMS'):
            Settings(**common, oidc_algorithms=algorithms)
    assert Settings(**common, oidc_algorithms='RS256, ES256').app_profile == 'production'


# --- over HTTP: the real routers behind the production identity ---------------------------------------------------

@pytest.fixture
def prod(monkeypatch, issuer):
    for key, value in {'APP_PROFILE': 'production', 'OIDC_ISSUER': ISSUER, 'OIDC_AUDIENCE': AUDIENCE, 'OPENAI_API_KEY': 'test-key'}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    app = FastAPI()
    register_error_handlers(app)
    for router in (routes.router, agents_routes.router, knowledge_routes.router): app.include_router(router)
    app.state.oidc = issuer.verifier()
    yield SimpleNamespace(app=app, http=httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://t'))
    get_settings.cache_clear()


def bearer(**over):
    return {'Authorization': f'Bearer {token(claims(**over))}'}


PROTECTED = [('GET', '/conversations'), ('POST', '/conversations'), ('GET', '/conversations/c1/messages'), ('POST', '/chat'),
             ('POST', '/conversations/c1/runs'), ('GET', '/runs/r1'), ('GET', '/runs/r1/events'), ('GET', '/runs/r1/evidence'),
             ('POST', '/runs/r1/cancel'), ('GET', '/agents'), ('POST', '/agents'), ('GET', '/knowledge/sources'),
             ('POST', '/knowledge/ingestions'), ('GET', '/knowledge/ingestions')]
ADMIN_ONLY = [(m, p) for m, p in PROTECTED if p.split('/')[1] in ('agents', 'knowledge')]


@pytest.mark.parametrize('method,path', PROTECTED)
async def test_every_protected_route_answers_401_without_a_valid_token(prod, method, path):
    for headers in ({}, {'Authorization': 'Bearer lixo'}, {'Authorization': 'Basic dXNlcjpwdw=='},
                    {'Authorization': f'Bearer {token(claims(exp=int(time.time()) - 600))}'}):
        response = await prod.http.request(method, f'/api/v1{path}', headers=headers, json={})  # 401 comes before body validation
        assert response.status_code == 401, (headers, response.text)
        assert response.json()['code'] == 'unauthorized' and response.json()['request_id']
        assert response.headers['WWW-Authenticate'].startswith('Bearer')


@pytest.mark.parametrize('method,path', ADMIN_ONLY)
async def test_operator_gets_403_on_admin_routes(prod, method, path):
    response = await prod.http.request(method, f'/api/v1{path}', headers=bearer(), json={})
    assert response.status_code == 403 and response.json()['code'] == 'forbidden'


@pytest.mark.parametrize('path,body', [('/chat', {'message': 'oi'}), ('/conversations/c1/runs', {'message': 'oi'})])
async def test_body_user_id_must_be_the_token_subject(prod, path, body):
    headers = {**bearer(sub='alice'), 'Idempotency-Key': 'k-12345678'}
    for forged_id in ('bob', 'cliente1988', 'alice '):
        response = await prod.http.post(f'/api/v1{path}', headers=headers, json={**body, 'user_id': forged_id})
        assert response.status_code == 403 and response.json()['code'] == 'forbidden', (path, forged_id, response.text)


async def test_demo_identity_is_not_used_in_production(prod):
    prod.app.state.oidc = None
    response = await prod.http.get('/api/v1/conversations', headers=bearer())
    assert response.status_code == 503 and response.json()['code'] == 'service_unavailable'
