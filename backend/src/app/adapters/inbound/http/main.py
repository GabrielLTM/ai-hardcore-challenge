import asyncio
from contextlib import asynccontextmanager, suppress
import httpx
from fastapi import FastAPI, HTTPException
from pymongo import AsyncMongoClient
from app.bootstrap.settings import get_settings
from app.bootstrap.telemetry import setup_telemetry
from app.adapters.outbound.mongo.seed import seed_demo
from app.adapters.outbound.mongo.conversations import MongoConversations
from app.adapters.inbound.http.errors import register_error_handlers
from app.adapters.inbound.http.execution import maintenance_loop
from app.adapters.inbound.http.routes import router
from app.adapters.inbound.http.run_settings import get_run_settings
from app.adapters.inbound.http.agents_routes import router as agents_router
from app.adapters.inbound.http.knowledge_routes import router as knowledge_router
from app.adapters.inbound.http.oidc import OidcVerifier


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings, cfg = get_settings(), get_run_settings()
    app.state.mongo = AsyncMongoClient(settings.mongo_uri, serverSelectionTimeoutMS=2000)
    idp = None
    if settings.app_profile == 'production':  # Settings already refused a production profile without issuer and audience
        idp = httpx.AsyncClient(timeout=5.0)
        app.state.oidc = OidcVerifier.from_settings(settings, idp)
    db = app.state.mongo[settings.mongo_database]
    await seed_demo(db, settings)
    await MongoConversations(db).ensure_indexes(cfg.event_retention_days)
    app.state.tasks = {}
    maintenance = asyncio.create_task(maintenance_loop(MongoConversations(db), cfg))
    yield
    maintenance.cancel()
    running = list(app.state.tasks.values())
    for task in running: task.cancel()  # each marks its run interrupted (no cancel was requested) before the client closes
    with suppress(Exception):
        await asyncio.wait_for(asyncio.gather(maintenance, *running, return_exceptions=True), timeout=10)
    await app.state.mongo.close()
    if idp: await idp.aclose()


app = FastAPI(title='Atendimento multiagente Getnet', version='0.1.0', lifespan=lifespan)
register_error_handlers(app)
app.include_router(router)
app.include_router(agents_router)
app.include_router(knowledge_router)
setup_telemetry(app, get_settings())


@app.get('/api/v1/health/live')
async def live():
    return {'status': 'ok'}


@app.get('/api/v1/health/ready')
async def ready():
    try:
        await app.state.mongo.admin.command('ping')
    except Exception:
        raise HTTPException(status_code=503, detail='Banco de dados indisponível') from None
    return {'status': 'ok'}
