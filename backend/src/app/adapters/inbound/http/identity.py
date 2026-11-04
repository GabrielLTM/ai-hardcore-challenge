from typing import Annotated
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.adapters.inbound.http.errors import ApiError
from app.bootstrap.settings import Settings, get_settings
from app.domain.identity import Identity

_bearer = HTTPBearer(auto_error=False)  # declares the scheme in OpenAPI; 401s are raised by the verifier


def demo_identity(settings: Settings, user_id: str | None = None) -> Identity:
    if settings.app_profile != 'local-demo':
        raise HTTPException(status_code=503, detail='Autenticação de produção indisponível')
    if user_id is not None and user_id != settings.demo_user_id:
        raise HTTPException(status_code=403, detail='Usuário não autorizado')
    return Identity(settings.demo_user_id, settings.demo_workspace_id, 'admin', is_demo=True)


async def current_identity(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None) -> Identity:
    """The caller, decided on the server: the demo user in `local-demo`, the verified OIDC token in `production`.

    `request.app.state.oidc` is the OidcVerifier built by the lifespan; production never falls back to the demo identity.
    """
    settings = get_settings()
    if settings.app_profile == 'local-demo':
        return demo_identity(settings)
    verifier = getattr(request.app.state, 'oidc', None)
    if verifier is None:
        raise ApiError(503, 'service_unavailable', 'Autenticação de produção indisponível.')
    return await verifier.authenticate(credentials.credentials if credentials else None)


Who = Annotated[Identity, Depends(current_identity)]


def bind_user(identity: Identity, user_id: str) -> Identity:
    """The `user_id` in a request body is only a claim by the client: it must be the authenticated user."""
    if user_id != identity.user_id:
        raise ApiError(403, 'forbidden', 'Usuário não autorizado.')
    return identity


def require_admin(identity: Identity) -> Identity:
    if identity.role != 'admin':
        raise ApiError(403, 'forbidden', 'Ação restrita a administradores.')
    return identity
