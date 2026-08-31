from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from ..auth.models import AppUser, UserRole
from ..db.alarm_control_repository import (
    AlarmControlError,
    set_out_of_service,
    set_suppressed,
    shelve_alarm,
    unshelve_alarm,
)
from ..runtime.commands import reset_alarm
from .dependencies import require_role

router = APIRouter(prefix="/alarms")
OperatorUser = Annotated[AppUser, Depends(require_role(UserRole.OPERATOR))]
EngineerUser = Annotated[AppUser, Depends(require_role(UserRole.ENGINEER))]


class ShelveRequest(BaseModel):
    duration_s: float = Field(gt=0, le=30 * 24 * 60 * 60)
    reason: str | None = Field(default=None, max_length=240)


class ControlReason(BaseModel):
    reason: str | None = Field(default=None, max_length=240)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


@router.post("/{alarm_id}/reset")
async def reset(
    alarm_id: str,
    request: Request,
    user: OperatorUser,
) -> dict[str, object]:
    try:
        result = reset_alarm(
            request.app.state.database,
            request.app.state.runtime_host.controller,
            alarm_id=alarm_id,
            user_id=user.user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="alarm not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {
        "alarm_id": alarm_id,
        "lifecycle": result.state.lifecycle.value,
        "latched": result.state.latched,
        "events": [event.event_type.value for event in result.events],
    }


@router.post("/{alarm_id}/shelve")
async def shelve(
    alarm_id: str,
    body: ShelveRequest,
    request: Request,
    user: OperatorUser,
) -> dict[str, object]:
    try:
        until = shelve_alarm(
            request.app.state.database,
            alarm_id,
            duration_s=body.duration_s,
            user_id=user.user_id,
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="alarm not found") from exc
    except AlarmControlError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"alarm_id": alarm_id, "shelved": True, "shelved_until": until.isoformat()}


@router.post("/{alarm_id}/unshelve")
async def unshelve(
    alarm_id: str,
    request: Request,
    user: OperatorUser,
) -> dict[str, object]:
    try:
        changed = unshelve_alarm(
            request.app.state.database,
            alarm_id,
            user_id=user.user_id,
            reason="OPERATOR",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="alarm not found") from exc
    return {"alarm_id": alarm_id, "shelved": False, "changed": changed}


@router.post("/{alarm_id}/suppress")
async def suppress(
    alarm_id: str,
    body: ControlReason,
    request: Request,
    user: EngineerUser,
) -> dict[str, object]:
    try:
        changed = set_suppressed(
            request.app.state.database,
            alarm_id,
            suppressed=True,
            user_id=user.user_id,
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="alarm not found") from exc
    except AlarmControlError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"alarm_id": alarm_id, "suppressed": True, "changed": changed}


@router.post("/{alarm_id}/unsuppress")
async def unsuppress(
    alarm_id: str,
    body: ControlReason,
    request: Request,
    user: EngineerUser,
) -> dict[str, object]:
    try:
        changed = set_suppressed(
            request.app.state.database,
            alarm_id,
            suppressed=False,
            user_id=user.user_id,
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="alarm not found") from exc
    return {"alarm_id": alarm_id, "suppressed": False, "changed": changed}


@router.post("/{alarm_id}/out-of-service")
async def take_out_of_service(
    alarm_id: str,
    body: ControlReason,
    request: Request,
    user: EngineerUser,
) -> dict[str, object]:
    try:
        changed = set_out_of_service(
            request.app.state.database,
            alarm_id,
            out_of_service=True,
            user_id=user.user_id,
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="alarm not found") from exc
    except AlarmControlError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"alarm_id": alarm_id, "out_of_service": True, "changed": changed}


@router.post("/{alarm_id}/in-service")
async def return_to_service(
    alarm_id: str,
    body: ControlReason,
    request: Request,
    user: EngineerUser,
) -> dict[str, object]:
    try:
        changed = set_out_of_service(
            request.app.state.database,
            alarm_id,
            out_of_service=False,
            user_id=user.user_id,
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="alarm not found") from exc
    return {"alarm_id": alarm_id, "out_of_service": False, "changed": changed}
