"""Admin curation of Reviewer verdicts (read-only, workspace-scoped)."""
from datetime import datetime, timedelta, timezone
from typing import Annotated
from fastapi import APIRouter, Query, Request
from app.adapters.inbound.http.agents_routes import Admin
from app.adapters.inbound.http.routes import jsonable, repository
from app.application.use_cases.curation import summarize_reviews

router = APIRouter(prefix='/api/v1/curation')


@router.get('/reviews')
async def reviews(request: Request, who: Admin, days: Annotated[int, Query(ge=1, le=90)] = 7):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return jsonable(summarize_reviews(await repository(request).review_records(who.workspace_id, since), days))
