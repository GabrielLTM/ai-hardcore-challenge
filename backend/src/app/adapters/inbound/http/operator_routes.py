"""Attendant area: demo login, the handoff queue and the actions on one ticket. The rules live in AttendanceService."""
from typing import Annotated, Literal
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from app.adapters.inbound.http.errors import ApiError
from app.adapters.inbound.http.operator_auth import CurrentOperator, issue_session
from app.adapters.inbound.http.routes import jsonable
from app.adapters.outbound.mongo.conversations import MongoConversations
from app.adapters.outbound.mongo.handoffs import MongoHandoffRepository
from app.adapters.outbound.mongo.operators import DEMO_OPERATORS, MongoOperatorRepository
from app.adapters.outbound.mongo.support import MongoSupportRepository
from app.application.use_cases.attendance import NOTICE_FOR_ATTENDANT, AttendanceService, customer_of
from app.bootstrap.settings import get_settings
from app.bootstrap.telemetry import record_handoff
from app.domain.handoff import CLOSED, IN_PROGRESS, OPEN, HandoffError

router = APIRouter(prefix='/api/v1/operator')

_TABS = {'abertos': (OPEN, False), 'meus': (IN_PROGRESS, True), 'encerrados': (CLOSED, False)}


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class TextInput(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class CloseInput(BaseModel):
    resolution: str = Field(min_length=1, max_length=2000)


def _db(request: Request):
    return request.app.state.mongo[get_settings().mongo_database]


def _demo_only():
    if get_settings().app_profile != 'local-demo':
        raise ApiError(404, 'not_found', 'Recurso não encontrado.')


HTTP_STATUS = {'handoff_not_found': 404, 'not_handoff_owner': 403, 'handoff_already_claimed': 409, 'invalid_transition': 409}


def http_error(exc: HandoffError) -> ApiError:
    return ApiError(HTTP_STATUS.get(exc.code, 409), exc.code, exc.message, details=exc.details)


def attendance(request: Request) -> AttendanceService:
    db = _db(request)
    return AttendanceService(MongoHandoffRepository(db), MongoConversations(db), record_handoff)


@router.get('/demo-accounts')
async def demo_accounts():
    _demo_only()
    password = get_settings().operator_demo_password.get_secret_value()
    return {'items': [{'name': name, 'email': email, 'password': password} for _, name, email in DEMO_OPERATORS]}


@router.post('/login')
async def login(payload: LoginInput, request: Request):
    _demo_only()
    operator = await MongoOperatorRepository(_db(request)).verify(payload.email, payload.password)
    if not operator:
        raise ApiError(401, 'invalid_credentials', 'E-mail ou senha inválidos.')
    token, expires = issue_session(operator)
    return jsonable({'token': token, 'expires_at': expires, 'operator': {'id': operator['id'], 'name': operator['name'], 'email': operator['email']}})


@router.get('/handoffs')
async def list_handoffs(request: Request, me: CurrentOperator, tab: Literal['abertos', 'meus', 'encerrados'] = 'abertos',
                        limit: Annotated[int, Query(ge=1, le=100)] = 30, cursor: str | None = None):
    status, mine = _TABS[tab]
    try:
        items, next_cursor = await MongoHandoffRepository(_db(request)).list_page(me.workspace_id, status, me.id if mine else None,
                                                                                  limit=limit, cursor=cursor)
    except ValueError:
        raise ApiError(400, 'invalid_cursor', 'Cursor de paginação inválido.') from None
    return jsonable({'items': items, 'next_cursor': next_cursor})


@router.get('/handoffs/{protocol}')
async def get_handoff(protocol: str, request: Request, me: CurrentOperator):
    db = _db(request)
    try:
        ticket = await attendance(request).ticket(me, protocol)
    except HandoffError as exc:
        raise http_error(exc) from None
    customer = customer_of(ticket)
    support = MongoSupportRepository(db)
    messages = await MongoConversations(db).messages(customer, ticket['conversation_id']) if ticket.get('conversation_id') else []
    receivables = [{'sale_date': r.sale_date, 'due_date': r.due_date, 'amount': str(r.amount), 'status': r.status, 'payment_type': r.payment_type}
                   for r in await support.get_receivables(customer)]
    terminals = [{'model': t.model, 'connectivity': t.connectivity, 'error': t.error, 'serial_suffix': t.serial_suffix}
                 for t in await support.get_terminals(customer)]
    shown = [{**m, 'content': NOTICE_FOR_ATTENDANT[m['content']]} if m.get('role') == 'system' and m.get('content') in NOTICE_FOR_ATTENDANT else m
             for m in messages if m.get('role') in ('user', 'assistant', 'operator', 'system')]  # notices are written to the customer
    return jsonable({'handoff': ticket, 'messages': shown,
                     'customer': {'user_id': ticket['user_id'], 'receivables': receivables, 'terminals': terminals}})


@router.post('/handoffs/{protocol}/claim')
async def claim(protocol: str, request: Request, me: CurrentOperator):
    try:
        return jsonable({'handoff': await attendance(request).claim(me, protocol)})
    except HandoffError as exc:
        raise http_error(exc) from None


@router.post('/handoffs/{protocol}/messages', status_code=201)
async def send_message(protocol: str, payload: TextInput, request: Request, me: CurrentOperator):
    content = payload.content.strip()
    if not content:
        raise ApiError(422, 'validation_error', 'A mensagem não pode ficar vazia.')
    try:
        return jsonable({'message': await attendance(request).send(me, protocol, content)})
    except HandoffError as exc:
        raise http_error(exc) from None


@router.post('/handoffs/{protocol}/notes', status_code=201)
async def add_note(protocol: str, payload: TextInput, request: Request, me: CurrentOperator):
    content = payload.content.strip()
    if not content:
        raise ApiError(422, 'validation_error', 'A nota não pode ficar vazia.')
    try:
        return jsonable({'handoff': await attendance(request).note(me, protocol, content)})
    except HandoffError as exc:
        raise http_error(exc) from None


@router.post('/handoffs/{protocol}/close')
async def close(protocol: str, payload: CloseInput, request: Request, me: CurrentOperator):
    resolution = payload.resolution.strip()
    if not resolution:
        raise ApiError(422, 'validation_error', 'Informe a nota de resolução para encerrar.')
    try:
        return jsonable({'handoff': await attendance(request).close(me, protocol, resolution)})
    except HandoffError as exc:
        raise http_error(exc) from None
