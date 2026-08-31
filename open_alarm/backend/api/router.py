from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ..auth.models import SUPPORTED_USER_LOCALES, AppUser, UserRole
from ..auth.repository import list_users, set_user_locale, set_user_role
from ..db.activation import RevisionActivationError, activate_revision
from ..db.alarm_query_repository import list_alarm_history
from ..engineering.repository import create_draft, get_draft, list_drafts
from ..engineering.service import create_revision_from_draft, preview_revision
from ..runtime.commands import acknowledge_alarm, acknowledge_all
from .dependencies import CurrentUser, require_role

router = APIRouter(prefix="/api")
ViewerUser = Annotated[AppUser, Depends(require_role(UserRole.VIEWER))]
OperatorUser = Annotated[AppUser, Depends(require_role(UserRole.OPERATOR))]
EngineerUser = Annotated[AppUser, Depends(require_role(UserRole.ENGINEER))]
AdminUser = Annotated[AppUser, Depends(require_role(UserRole.ADMIN))]


class LocaleUpdate(BaseModel):
    locale: str


class RoleUpdate(BaseModel):
    role: UserRole


class DraftCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    clone_active: bool = True


def _user_payload(user: AppUser) -> dict[str, object]:
    return {
        "user_id": user.user_id,
        "user_name": user.user_name,
        "display_name": user.display_name,
        "role": user.role.value,
        "locale": user.locale,
    }


@router.get("/session")
async def session(user: CurrentUser) -> dict[str, object]:
    return _user_payload(user)


@router.put("/session/locale")
async def update_locale(
    body: LocaleUpdate,
    request: Request,
    user: CurrentUser,
) -> dict[str, object]:
    if body.locale not in SUPPORTED_USER_LOCALES:
        raise HTTPException(status_code=422, detail="unsupported locale")
    updated = set_user_locale(
        request.app.state.database,
        user_id=user.user_id,
        locale=body.locale,
    )
    return _user_payload(updated)


@router.get("/alarms/history")
async def alarm_history(
    request: Request,
    user: ViewerUser,
    alarm_id: str | None = None,
    before_event_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, object]]:
    del user
    return list_alarm_history(
        request.app.state.database,
        alarm_id=alarm_id,
        before_event_id=before_event_id,
        limit=limit,
    )


@router.post("/alarms/{alarm_id}/ack")
async def ack_alarm(
    alarm_id: str,
    request: Request,
    user: OperatorUser,
) -> dict[str, object]:
    try:
        result = acknowledge_alarm(
            request.app.state.database,
            request.app.state.runtime_host.controller,
            alarm_id=alarm_id,
            user_id=user.user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="alarm not found") from exc
    return {
        "alarm_id": alarm_id,
        "lifecycle": result.state.lifecycle.value,
        "events": [event.event_type.value for event in result.events],
    }


@router.post("/alarms/ack-all")
async def ack_all_alarms(
    request: Request,
    user: OperatorUser,
) -> dict[str, object]:
    alarm_ids = acknowledge_all(
        request.app.state.database,
        request.app.state.runtime_host.controller,
        user_id=user.user_id,
    )
    return {"acknowledged": list(alarm_ids), "count": len(alarm_ids)}


@router.get("/engineering/drafts")
async def drafts(
    request: Request,
    user: EngineerUser,
) -> list[dict[str, object]]:
    del user
    return list_drafts(request.app.state.database)


@router.post("/engineering/drafts")
async def new_draft(
    body: DraftCreate,
    request: Request,
    user: EngineerUser,
) -> dict[str, object]:
    draft_id = create_draft(
        request.app.state.database,
        name=body.name,
        created_by=user.user_id,
        clone_active=body.clone_active,
    )
    return get_draft(request.app.state.database, draft_id) or {}


@router.post("/engineering/drafts/{draft_id}/review")
async def review_engineering_draft(
    draft_id: str,
    request: Request,
    user: EngineerUser,
) -> dict[str, object]:
    try:
        revision_id, result = create_revision_from_draft(
            request.app.state.database,
            draft_id,
            user_id=user.user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="draft not found") from exc
    return {
        "ok": result.ok,
        "revision_id": revision_id,
        "issues": [asdict(issue) for issue in result.issues],
        "source_hash": None if result.compiled is None else result.compiled.source_hash,
        "preview": (
            None
            if revision_id is None
            else preview_revision(request.app.state.database, revision_id)
        ),
    }


@router.post("/engineering/revisions/{revision_id}/activate")
async def activate_engineering_revision(
    revision_id: str,
    request: Request,
    user: AdminUser,
) -> dict[str, object]:
    try:
        result = activate_revision(
            request.app.state.database,
            revision_id,
            user_id=user.user_id,
        )
    except RevisionActivationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await request.app.state.runtime_host.reload()
    return {
        "active_revision_id": result.active_revision_id,
        "previous_revision_id": result.previous_revision_id,
        "migrated_alarm_ids": list(result.migrated_alarm_ids),
        "reset_alarm_ids": list(result.reset_alarm_ids),
        "already_active": result.already_active,
    }


@router.get("/admin/users")
async def users(
    request: Request,
    user: AdminUser,
) -> list[dict[str, object]]:
    del user
    return [_user_payload(item) for item in list_users(request.app.state.database)]


@router.put("/admin/users/{user_id}/role")
async def update_role(
    user_id: str,
    body: RoleUpdate,
    request: Request,
    actor: AdminUser,
) -> dict[str, object]:
    try:
        updated = set_user_role(
            request.app.state.database,
            actor_user_id=actor.user_id,
            target_user_id=user_id,
            role=body.role,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _user_payload(updated)
