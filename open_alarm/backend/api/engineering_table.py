from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth.models import AppUser, UserRole
from ..engineering.alarm_table import (
    load_alarm_table,
    load_notification_groups,
    next_alarm_id,
    next_notification_group_id,
    replace_alarm_table,
)
from ..engineering.repository import DraftConflictError
from ..ha.client import HomeAssistantConnectionError
from ..ha.entity_registry import ENTITY_PREVIEW_LIMIT, HomeAssistantEntityRegistryClient
from .dependencies import require_role

router = APIRouter(prefix="/api/engineering")
EngineerUser = Annotated[AppUser, Depends(require_role(UserRole.ENGINEER))]


class AlarmTableRow(BaseModel):
    alarm_id: int = Field(ge=1)
    entity_id: str = Field(min_length=1, max_length=255)
    kind: Literal["ANALOG", "DIGITAL", "DEVICE"] = "ANALOG"
    condition: str = Field(default="EQUALS", min_length=1, max_length=64)
    hihi: float | None = None
    hi: float | None = None
    lo: float | None = None
    lolo: float | None = None
    alarm_value: str | None = None
    priority: str = Field(default="P2", min_length=1, max_length=32)
    category: str = Field(default="PROCESS", min_length=1, max_length=64)
    hysteresis: float = Field(default=0, ge=0)
    debounce_on_s: float = Field(default=0, ge=0)
    debounce_off_s: float = Field(default=0, ge=0)
    on_delay_s: float = Field(default=0, ge=0)
    off_delay_s: float = Field(default=0, ge=0)
    stale_after_s: float | None = Field(default=None, ge=0)
    message: str = Field(default="", max_length=500)
    notification_group_id: int | None = Field(default=None, ge=1)
    enabled: bool = True
    row_order: int = 0


class NotificationGroupRow(BaseModel):
    group_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    title: str = Field(default="Open Alarm", min_length=1, max_length=120)
    target_entity_ids: list[str] = Field(min_length=1, max_length=100)
    notify_delay_s: float = Field(default=0, ge=0)
    enabled: bool = True
    row_order: int = 0


class AlarmTableReplace(BaseModel):
    expected_updated_at: str = Field(min_length=1)
    rows: list[AlarmTableRow] = Field(default_factory=list, max_length=10000)
    notification_groups: list[NotificationGroupRow] = Field(default_factory=list, max_length=500)


class EntityPreviewRequest(BaseModel):
    entity_ids: list[str] = Field(min_length=1, max_length=ENTITY_PREVIEW_LIMIT)


@router.get("/entities")
async def engineering_entities(
    user: EngineerUser,
) -> list[dict[str, str | None]]:
    del user
    try:
        return await HomeAssistantEntityRegistryClient().fetch_entities_for_display()
    except HomeAssistantConnectionError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"cannot load Home Assistant entity list: {exc}",
        ) from exc


@router.post("/entity-values")
async def engineering_entity_values(
    body: EntityPreviewRequest,
    user: EngineerUser,
) -> dict[str, dict[str, str | None]]:
    del user
    if any(not entity_id.strip() or len(entity_id) > 255 for entity_id in body.entity_ids):
        raise HTTPException(status_code=422, detail="invalid Home Assistant entity ID")
    try:
        return await HomeAssistantEntityRegistryClient().fetch_entity_state_previews(body.entity_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HomeAssistantConnectionError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"cannot load Home Assistant entity values: {exc}",
        ) from exc


@router.get("/drafts/{draft_id}/alarm-table")
async def alarm_table(
    draft_id: str,
    request: Request,
    user: EngineerUser,
) -> list[dict[str, object]]:
    del user
    try:
        return load_alarm_table(request.app.state.database, draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="draft not found") from exc


@router.get("/drafts/{draft_id}/notification-groups")
async def notification_groups(
    draft_id: str,
    request: Request,
    user: EngineerUser,
) -> list[dict[str, object]]:
    del user
    try:
        return load_notification_groups(request.app.state.database, draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="draft not found") from exc


@router.get("/drafts/{draft_id}/next-alarm-id")
async def next_alarm_table_id(
    draft_id: str,
    request: Request,
    user: EngineerUser,
) -> dict[str, int]:
    del user
    try:
        return {"alarm_id": next_alarm_id(request.app.state.database, draft_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="draft not found") from exc


@router.get("/drafts/{draft_id}/next-notification-group-id")
async def next_group_id(
    draft_id: str,
    request: Request,
    user: EngineerUser,
) -> dict[str, int]:
    del user
    try:
        return {"group_id": next_notification_group_id(request.app.state.database, draft_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="draft not found") from exc


@router.put("/drafts/{draft_id}/alarm-table")
async def save_alarm_table(
    draft_id: str,
    body: AlarmTableReplace,
    request: Request,
    user: EngineerUser,
) -> dict[str, object]:
    try:
        updated_at = replace_alarm_table(
            request.app.state.database,
            draft_id=draft_id,
            rows=[row.model_dump() for row in body.rows],
            groups=[group.model_dump() for group in body.notification_groups],
            notification_locale=user.locale,
            expected_updated_at=body.expected_updated_at,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="draft not found") from exc
    except DraftConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "engineering draft changed on the server; reload before saving",
                "current_updated_at": exc.current_updated_at,
            },
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "saved": len(body.rows),
        "saved_notification_groups": len(body.notification_groups),
        "updated_at": updated_at,
    }
