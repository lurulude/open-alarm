from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..auth.models import AppUser, UserRole
from ..db.notification_outbox import retry_failed_notifications
from ..notifications.status import notification_outbox_status
from .dependencies import require_role

router = APIRouter(prefix="/api/admin/notifications", tags=["notifications"])
AdminUser = Annotated[AppUser, Depends(require_role(UserRole.ADMIN))]


class RetryFailedNotificationsRequest(BaseModel):
    outbox_ids: list[int] | None = Field(default=None, max_length=500)


@router.get("/status")
async def notification_status(request: Request, user: AdminUser) -> dict[str, object]:
    del user
    payload = notification_outbox_status(request.app.state.database)
    payload["worker_running"] = request.app.state.runtime_host.notification_worker.running
    return payload


@router.post("/retry-failed")
async def retry_failed(
    body: RetryFailedNotificationsRequest,
    request: Request,
    user: AdminUser,
) -> dict[str, object]:
    ids = retry_failed_notifications(
        request.app.state.database,
        outbox_ids=None if body.outbox_ids is None else tuple(body.outbox_ids),
    )
    request.app.state.runtime_host.system_alarms.record_runtime_event(
        "NOTIFICATION_RETRY",
        details={"user_id": user.user_id, "outbox_ids": list(ids), "count": len(ids)},
    )
    request.app.state.runtime_host.health_once()
    return {"retried": len(ids), "outbox_ids": list(ids)}
