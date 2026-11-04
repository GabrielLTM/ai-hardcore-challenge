"""Structured API errors: every route answers {code, message, request_id, details?}."""
import re
import uuid
from http import HTTPStatus
from typing import Any
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.domain.errors import CapacityExceeded, ConversationBusy, ConversationNotFound, IdempotencyConflict


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


_HTTP_CODES = {400: 'bad_request', 401: 'unauthorized', 403: 'forbidden', 404: 'not_found', 405: 'method_not_allowed',
               409: 'conflict', 410: 'gone', 412: 'precondition_failed', 422: 'validation_error', 429: 'too_many_requests',
               503: 'service_unavailable'}
_DOMAIN = {
    ConversationNotFound: ApiError(404, 'conversation_not_found', 'Conversa não encontrada.'),
    ConversationBusy: ApiError(409, 'conversation_busy', 'Já existe uma execução em andamento nesta conversa.'),
    IdempotencyConflict: ApiError(409, 'idempotency_conflict', 'A chave de idempotência já foi usada com outro conteúdo.'),
    CapacityExceeded: ApiError(429, 'capacity_exceeded', 'Capacidade de execuções simultâneas atingida. Tente novamente em instantes.',
                               headers={'Retry-After': '5'}),
}
_HTTP_MESSAGES = {404: 'Recurso não encontrado.', 405: 'Método não permitido.'}  # Starlette's defaults are English
_REQUEST_ID = re.compile(r'^[A-Za-z0-9._-]{8,64}$')


def request_id_of(request: Request) -> str:
    return getattr(request.state, 'request_id', None) or uuid.uuid4().hex


def _response(request: Request, status: int, code: str, message: str, details: Any = None, headers: dict | None = None):
    request_id = request_id_of(request)
    body = {'code': code, 'message': message, 'request_id': request_id}
    if details is not None:
        body['details'] = details
    # set here too: 500s are answered by ServerErrorMiddleware, outside RequestIdMiddleware
    return JSONResponse(body, status_code=status, headers={**(headers or {}), 'X-Request-ID': request_id})


class RequestIdMiddleware:
    """Pure ASGI (no response buffering, so SSE keeps streaming): honours a sane X-Request-ID or generates one."""

    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        incoming = dict(scope['headers']).get(b'x-request-id', b'').decode('latin-1')
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault('state', {})['request_id'] = request_id

        async def send_with_id(message):
            if message['type'] == 'http.response.start' and not any(k.lower() == b'x-request-id' for k, _ in message.get('headers', [])):
                message['headers'] = [*message.get('headers', []), (b'x-request-id', request_id.encode())]
            await send(message)
        await self.app(scope, receive, send_with_id)


def register_error_handlers(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return _response(request, exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        message = exc.detail if isinstance(exc.detail, str) else 'Requisição não pôde ser atendida.'
        if message == HTTPStatus(exc.status_code).phrase:
            message = _HTTP_MESSAGES.get(exc.status_code, message)
        return _response(request, exc.status_code, _HTTP_CODES.get(exc.status_code, 'http_error'), message,
                         headers=getattr(exc, 'headers', None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{'loc': [str(p) for p in e['loc']], 'message': e['msg'], 'type': e['type']} for e in exc.errors()]  # never echo input
        return _response(request, 422, 'validation_error', 'Requisição inválida.', details)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        return _response(request, 500, 'internal_error', 'Erro interno. Informe o request_id ao suporte.')

    for exc_type, template in _DOMAIN.items():
        async def domain_error(request: Request, exc: Exception, template=template):
            return _response(request, template.status, template.code, template.message, headers=template.headers)
        app.add_exception_handler(exc_type, domain_error)
