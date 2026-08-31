from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ..auth.models import AppUser, UserRole
from ..auth.repository import resolve_ingress_user
from ..ha.client import HomeAssistantConnectionError


async def current_user(request: Request) -> AppUser:
    user_id = request.headers.get("X-Remote-User-Id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Home Assistant Ingress user identity is required",
        )

    try:
        ha_admin_verified = await request.app.state.ha_admin_authorizer.is_active_admin(user_id)
    except HomeAssistantConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Home Assistant user authorization could not be verified",
        ) from exc
    if not ha_admin_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An active Home Assistant administrator is required",
        )

    return resolve_ingress_user(
        request.app.state.database,
        user_id=user_id,
        user_name=request.headers.get("X-Remote-User-Name"),
        display_name=request.headers.get("X-Remote-User-Display-Name"),
    )


CurrentUser = Annotated[AppUser, Depends(current_user)]


def require_role(minimum: UserRole) -> Callable[..., AppUser]:
    async def dependency(user: CurrentUser) -> AppUser:
        if not user.has_role(minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{minimum.value} role required",
            )
        return user

    return dependency
