from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth.models import AppUser, UserRole
from ..db.alarm_browser import alarm_browser_summary, browse_alarm_states
from .dependencies import require_role

router = APIRouter(prefix="/api/alarm-browser")
ViewerUser = Annotated[AppUser, Depends(require_role(UserRole.VIEWER))]


@router.get("")
async def alarm_browser(
    request: Request,
    user: ViewerUser,
    view: str = Query(default="active"),
    priority: str | None = None,
    category: str | None = None,
    search: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[dict[str, object]]:
    del user
    try:
        return browse_alarm_states(
            request.app.state.database,
            view=view,
            priority=priority,
            category=category,
            search=search,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/summary")
async def alarm_summary(
    request: Request,
    user: ViewerUser,
) -> dict[str, object]:
    del user
    return alarm_browser_summary(request.app.state.database)
