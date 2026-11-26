"""Attendant (human operator) sessions: HS256 tokens signed by this API in `local-demo`; the OIDC `attendant` role in `production`."""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Annotated
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.adapters.inbound.http.errors import ApiError
from app.bootstrap.settings import get_settings
from app.domain.identity import Operator

log = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)
ISSUER = 'getnet-operator-session'
SESSION_HOURS = 8


@lru_cache
def session_secret() -> str:
    configured = get_settings().operator_session_secret
    if configured and configured.get_secret_value():
        return configured.get_secret_value()
    log.warning('OPERATOR_SESSION_SECRET vazio: usando um segredo deste processo; sessões de atendente caem ao reiniciar a API.')
    return secrets.token_urlsafe(32)


def issue_session(operator: dict, now: datetime | None = None) -> tuple[str, datetime]:
    now = now or datetime.now(timezone.utc)
    expires = now + timedelta(hours=SESSION_HOURS)
    claims = {'iss': ISSUER, 'sub': operator['id'], 'name': operator['name'], 'workspace_id': operator['workspace_id'],
              'role': 'attendant', 'iat': now, 'exp': expires}
    return jwt.encode(claims, session_secret(), algorithm='HS256'), expires


def read_session(token: str) -> Operator:
    try:
        claims = jwt.decode(token, session_secret(), algorithms=['HS256'], issuer=ISSUER, options={'require': ['exp', 'sub', 'iss']})
    except jwt.ExpiredSignatureError:
        raise ApiError(401, 'invalid_token', 'Sessão de atendente expirada. Entre novamente.') from None
    except jwt.PyJWTError:
        raise ApiError(401, 'invalid_token', 'Sessão de atendente inválida.') from None
    if claims.get('role') != 'attendant':
        raise ApiError(401, 'invalid_token', 'Sessão de atendente inválida.')
    return Operator(str(claims['sub']), str(claims.get('name') or claims['sub']), str(claims['workspace_id']))


def is_operator_session(token: str) -> bool:
    try:
        read_session(token)
        return True
    except ApiError:
        return False


async def current_operator(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None) -> Operator:
    token = credentials.credentials if credentials else None
    if get_settings().app_profile == 'local-demo':
        if not token:
            raise ApiError(401, 'unauthorized', 'Entre na área do atendente.')
        return read_session(token)
    verifier = getattr(request.app.state, 'oidc', None)
    if verifier is None:
        raise ApiError(503, 'service_unavailable', 'Autenticação de produção indisponível.')
    identity = await verifier.authenticate(token)
    if identity.role != 'attendant':
        raise ApiError(403, 'forbidden', 'Ação restrita a atendentes.')
    return Operator(identity.user_id, identity.user_id, identity.workspace_id)


CurrentOperator = Annotated[Operator, Depends(current_operator)]
