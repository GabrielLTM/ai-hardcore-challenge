"""Agent administration endpoints (drafts, relationships, publication, versions). Admin only."""
import re
from typing import Annotated, Any, Literal
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field, StringConstraints, field_validator
from app.adapters.inbound.http.errors import ApiError
from app.adapters.inbound.http.identity import current_identity, require_admin
from app.adapters.outbound.mongo.agents import MongoAgentRepository
from app.application.use_cases.agents import AgentService
from app.bootstrap.settings import get_settings
from app.domain.agents import AgentError
from app.domain.identity import Identity
from app.domain.models import AgentKind

router = APIRouter(prefix='/api/v1')

STATUS = {'agent_not_found': 404, 'version_not_found': 404, 'prompt_not_found': 404, 'invalid_cursor': 400,
          'revision_conflict': 412, 'graph_invalid': 409, 'graph_conflict': 409, 'no_changes': 409, 'id_conflict': 409,
          'secret_detected': 422}

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Instructions = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]
ModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ToolName = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]{0,63}$')]
AgentId = Annotated[str, StringConstraints(pattern=r'^[a-z0-9][a-z0-9_-]{0,63}$')]


class Limits(BaseModel, extra='forbid'):
    max_output_tokens: int | None = Field(None, ge=1, le=64000)
    max_tool_calls: int | None = Field(None, ge=1, le=50)


class AgentPatch(BaseModel, extra='forbid'):
    """Every field optional: only the fields sent are changed (null is ignored)."""
    name: Name | None = None
    description: Text | None = None
    kind: AgentKind | None = None
    instructions: Instructions | None = None
    model: ModelName | None = None
    tools: list[ToolName] | None = Field(None, max_length=20)
    delegates: list[AgentId] | None = Field(None, max_length=50)
    limits: Limits | None = None
    enabled: bool | None = None

    @field_validator('tools', 'delegates')
    @classmethod
    def unique(cls, value):
        return list(dict.fromkeys(value)) if value else value


class AgentCreate(AgentPatch):
    name: Name
    instructions: Instructions


class Proposal(BaseModel, extra='forbid'):
    delegates: list[AgentId] | None = Field(None, max_length=50)
    tools: list[ToolName] | None = Field(None, max_length=20)
    enabled: bool | None = None
    graph_revision: int | None = Field(None, ge=0)


class Publish(BaseModel, extra='forbid'):
    summary: Text | None = None


def admin(identity: Annotated[Identity, Depends(current_identity)]) -> Identity:
    return require_admin(identity)


Admin = Annotated[Identity, Depends(admin)]
Limit = Annotated[int, Query(ge=1, le=100)]


def service(request: Request) -> AgentService:
    settings = get_settings()
    return AgentService(MongoAgentRepository(request.app.state.mongo[settings.mongo_database]), settings.openai_model)


async def run(coro):
    try:
        return await coro
    except AgentError as exc:
        raise ApiError(STATUS[exc.code], exc.code, exc.message, exc.details) from None


def revision(if_match: str | None, required: bool = False) -> int | None:
    if if_match is None:
        if required:
            raise ApiError(428, 'precondition_required', 'Envie o cabeçalho If-Match com a revisão carregada.')
        return None
    found = re.fullmatch(r'(?:W/)?"?(\d+)"?', if_match.strip())
    if not found:
        raise ApiError(400, 'invalid_if_match', 'If-Match deve conter a revisão numérica do agente.')
    return int(found.group(1))


def tagged(response: Response, agent: dict[str, Any]) -> dict[str, Any]:
    response.headers['ETag'] = f"\"{agent['revision']}\""
    return agent


IfMatch = Annotated[str | None, Header(alias='If-Match')]


@router.get('/agents')
async def list_agents(request: Request, who: Admin, q: Annotated[str | None, Query(max_length=100)] = None,
                      kind: AgentKind | None = None, status: Literal['draft', 'published'] | None = None,
                      limit: Limit = 20, cursor: str | None = None):
    return await run(service(request).list_agents(who, q=q, kind=kind, status=status, limit=limit, cursor=cursor))


@router.post('/agents', status_code=201)
async def create_agent(payload: AgentCreate, request: Request, response: Response, who: Admin):
    return tagged(response, await run(service(request).create(who, payload.model_dump(mode='json', exclude_none=True))))


@router.get('/agents/{agent_id}')
async def get_agent(agent_id: str, request: Request, response: Response, who: Admin):
    return tagged(response, await run(service(request).get(who, agent_id)))


@router.patch('/agents/{agent_id}')
async def update_agent(agent_id: str, payload: AgentPatch, request: Request, response: Response, who: Admin, if_match: IfMatch = None):
    expected = revision(if_match, required=True)
    return tagged(response, await run(service(request).update(who, agent_id, expected, payload.model_dump(mode='json', exclude_none=True))))


@router.get('/agents/{agent_id}/relationships')
async def relationships(agent_id: str, request: Request, who: Admin, state: Literal['draft', 'published'] = 'published'):
    return await run(service(request).relationships(who, agent_id, state))


@router.post('/agents/{agent_id}/relationships/validate')
async def validate_relationships(agent_id: str, payload: Proposal, request: Request, who: Admin):
    return await run(service(request).validate(who, agent_id, payload.model_dump(exclude_none=True)))


@router.post('/agents/{agent_id}/publish', status_code=201)
async def publish_agent(agent_id: str, request: Request, who: Admin, payload: Publish = Publish(), if_match: IfMatch = None):
    return await run(service(request).publish(who, agent_id, if_match=revision(if_match), summary=payload.summary))


@router.get('/agents/{agent_id}/versions')
async def agent_versions(agent_id: str, request: Request, who: Admin, limit: Limit = 20, cursor: str | None = None):
    return await run(service(request).versions(who, agent_id, limit=limit, cursor=cursor))


@router.get('/agents/{agent_id}/versions/{version}')
async def agent_version(agent_id: str, version: int, request: Request, who: Admin):
    return await run(service(request).version(who, agent_id, version))


@router.get('/agents/{agent_id}/versions/{a}/diff/{b}')
async def diff_versions(agent_id: str, a: int, b: int, request: Request, who: Admin):
    return await run(service(request).diff(who, agent_id, a, b))


@router.post('/agents/{agent_id}/versions/{version}/restore', status_code=201)
async def restore_version(agent_id: str, version: int, request: Request, who: Admin, payload: Publish = Publish(), if_match: IfMatch = None):
    return await run(service(request).restore(who, agent_id, version, if_match=revision(if_match), summary=payload.summary))


@router.get('/prompts/{prompt_id}/versions')
async def prompt_versions(prompt_id: str, request: Request, who: Admin, limit: Limit = 20, cursor: str | None = None):
    return await run(service(request).prompt_versions(who, prompt_id, limit=limit, cursor=cursor))


@router.get('/graph/versions')
async def graph_versions(request: Request, who: Admin, limit: Limit = 20, cursor: str | None = None):
    return await run(service(request).graph_versions(who, limit=limit, cursor=cursor))
