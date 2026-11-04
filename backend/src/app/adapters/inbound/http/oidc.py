"""Production identity: OIDC bearer JWTs verified against the issuer's JWKS, claims mapped to an Identity.

401 means the token is missing/invalid/expired (the client should authenticate again); 403 means the token is valid
but carries no usable workspace or role. TLS terminates in the infrastructure; the issuer itself is reached over https.
"""
import asyncio
import logging
import re
import time
from typing import Any
import httpx
import jwt
from jwt import PyJWK, PyJWKSet
from app.adapters.inbound.http.errors import ApiError
from app.bootstrap.settings import Settings, csv
from app.domain.identity import Identity

log = logging.getLogger(__name__)
_WORKSPACE = re.compile(r'[A-Za-z0-9._:-]{1,64}')
_REQUIRED = ['exp', 'iss', 'aud', 'sub']


def _unauthorized(message: str, error: str | None = None) -> ApiError:
    challenge = 'Bearer' + (f' error="{error}"' if error else '')  # RFC 6750: no error attribute when no token was sent
    return ApiError(401, 'unauthorized', message, headers={'WWW-Authenticate': challenge})


def _unavailable() -> ApiError:
    return ApiError(503, 'service_unavailable', 'Provedor de identidade indisponível. Tente novamente em instantes.',
                    headers={'Retry-After': '5'})


def _claim(claims: dict[str, Any], path: str) -> Any:
    """`path` is a literal claim name (may be a URL) or a dotted path into nested objects (realm_access.roles)."""
    if path in claims:
        return claims[path]
    value: Any = claims
    for part in path.split('.'):
        value = value.get(part) if isinstance(value, dict) else None
    return value


class OidcVerifier:
    def __init__(self, http: httpx.AsyncClient, *, issuer: str, audience: str, jwks_uri: str | None = None,
                 algorithms: tuple[str, ...] = ('RS256',), workspace_claim: str = 'workspace_id',
                 allowed_workspaces: tuple[str, ...] = (), roles_claim: str = 'roles', admin_role: str = 'admin',
                 operator_role: str = 'operator', leeway: int = 30, jwks_ttl: float = 600, min_refresh: float = 30,
                 clock=time.monotonic):
        self.http, self.issuer, self.audience, self.jwks_uri = http, issuer, audience, jwks_uri
        self.algorithms, self.workspace_claim, self.allowed_workspaces = list(algorithms), workspace_claim, set(allowed_workspaces)
        self.roles_claim, self.admin_role, self.operator_role = roles_claim, admin_role, operator_role
        self.leeway, self.jwks_ttl, self.min_refresh, self._clock = leeway, jwks_ttl, min_refresh, clock
        self._keys: dict[str | None, PyJWK] = {}
        self._fetched_at: float | None = None  # last successful fetch (TTL)
        self._attempted_at: float | None = None  # last attempt, successful or not (rate limit)
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings, http: httpx.AsyncClient) -> 'OidcVerifier':
        return cls(http, issuer=settings.oidc_issuer, audience=settings.oidc_audience, jwks_uri=settings.oidc_jwks_uri,
                   algorithms=tuple(csv(settings.oidc_algorithms)), workspace_claim=settings.oidc_workspace_claim,
                   allowed_workspaces=tuple(csv(settings.oidc_allowed_workspaces)), roles_claim=settings.oidc_roles_claim,
                   admin_role=settings.oidc_admin_role, operator_role=settings.oidc_operator_role,
                   leeway=settings.oidc_leeway_seconds, jwks_ttl=settings.oidc_jwks_ttl_seconds,
                   min_refresh=settings.oidc_jwks_min_refresh_seconds)

    async def _get(self, url: str) -> dict:
        response = await self.http.get(url)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError('resposta inesperada')
        return data

    async def _fetch_keys(self) -> dict[str | None, PyJWK]:
        uri = self.jwks_uri
        if not uri:
            document = await self._get(f"{self.issuer.rstrip('/')}/.well-known/openid-configuration")
            if document.get('issuer') != self.issuer:
                raise ValueError('o issuer descoberto difere do configurado')
            uri = document['jwks_uri']
        if not str(uri).startswith('https://'):
            raise ValueError('jwks_uri deve usar https')
        keys = [k for k in (await self._get(uri)).get('keys', []) if isinstance(k, dict) and k.get('use', 'sig') == 'sig']
        return {k.key_id: k for k in PyJWKSet.from_dict({'keys': keys}).keys}

    async def _refresh(self, seen: float | None):
        async with self._lock:
            if self._attempted_at != seen:
                return  # another request refreshed while this one waited for the lock
            self._attempted_at = self._clock()
            try:
                self._keys, self._fetched_at = await self._fetch_keys(), self._attempted_at
            except Exception as exc:  # network, status, JSON, bad JWKS: keep serving the keys already known
                log.warning('Falha ao obter o JWKS do emissor OIDC (%s: %s).', type(exc).__name__, exc)

    async def _key(self, kid: str | None) -> PyJWK | None:
        """Cached key; refetched when the cache is past its TTL or `kid` is unknown (rotation), at most once per `min_refresh`."""
        now = self._clock()
        stale = self._fetched_at is None or now - self._fetched_at >= self.jwks_ttl
        if kid in self._keys and not stale:
            return self._keys[kid]
        if self._attempted_at is None or now - self._attempted_at >= self.min_refresh:
            await self._refresh(self._attempted_at)
        if not self._keys:
            raise _unavailable()
        return self._keys.get(kid)  # a known key stays valid while the issuer is unreachable (fail-static)

    async def authenticate(self, token: str | None) -> Identity:
        if not token:
            raise _unauthorized('Autenticação necessária.')
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get('kid')
            if header.get('alg') not in self.algorithms or not (kid is None or isinstance(kid, str)):
                raise jwt.InvalidTokenError('cabeçalho não aceito')  # `none`, HS* and unlisted algorithms stop here
            jwk = await self._key(kid)
            if jwk is None:
                raise jwt.InvalidKeyError('chave desconhecida')
            claims = jwt.decode(token, jwk.key, algorithms=[header['alg']], audience=self.audience, issuer=self.issuer,
                                leeway=self.leeway, options={'require': _REQUIRED})
        except jwt.ExpiredSignatureError:
            raise _unauthorized('Sessão expirada. Autentique-se novamente.', 'invalid_token') from None
        except (jwt.PyJWTError, TypeError, ValueError):
            raise _unauthorized('Token inválido.', 'invalid_token') from None
        return self.identity(claims)

    def identity(self, claims: dict[str, Any]) -> Identity:
        """Signature and standard claims are already verified; map `sub`, workspace and role without trusting anything else."""
        sub = claims['sub']
        if not isinstance(sub, str) or not 0 < len(sub) <= 200:
            raise _unauthorized('Token inválido.', 'invalid_token')
        workspace = _claim(claims, self.workspace_claim)
        if not isinstance(workspace, str) or not _WORKSPACE.fullmatch(workspace) \
                or (self.allowed_workspaces and workspace not in self.allowed_workspaces):
            raise ApiError(403, 'forbidden', 'Usuário sem workspace autorizado.')
        roles = _claim(claims, self.roles_claim)
        roles = {roles} if isinstance(roles, str) else {r for r in roles if isinstance(r, str)} if isinstance(roles, list) else set()
        role = 'admin' if self.admin_role in roles else 'operator' if self.operator_role in roles else None
        if role is None:
            raise ApiError(403, 'forbidden', 'Usuário sem papel autorizado.')
        return Identity(sub, workspace, role)
