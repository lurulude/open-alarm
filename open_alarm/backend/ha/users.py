from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any

from websockets.asyncio.client import connect as websocket_connect

from .client import HomeAssistantConnectionError, HomeAssistantWebSocketClient

HOME_ASSISTANT_ADMIN_GROUP_ID = "system-admin"


@dataclass(frozen=True, slots=True)
class HomeAssistantUser:
    user_id: str
    username: str | None
    name: str | None
    is_owner: bool
    is_active: bool
    system_generated: bool
    group_ids: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return HOME_ASSISTANT_ADMIN_GROUP_ID in self.group_ids


class HomeAssistantUserDirectoryClient(HomeAssistantWebSocketClient):
    async def fetch_users(self) -> tuple[HomeAssistantUser, ...]:
        async with websocket_connect(
            self.url,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_queue=1024,
        ) as websocket:
            await self._authenticate(websocket)
            command_id = self._command_id()
            await self._send(websocket, {"id": command_id, "type": "config/auth/list"})
            result = await self._wait_for_result(websocket, command_id, [])
        return parse_user_list(result)

    async def fetch_user(self, user_id: str) -> HomeAssistantUser | None:
        normalized_id = user_id.strip()
        if not normalized_id:
            raise ValueError("user_id is required")
        return next((user for user in await self.fetch_users() if user.user_id == normalized_id), None)


@dataclass(frozen=True, slots=True)
class _CachedAdminResult:
    allowed: bool
    expires_at: float


class HomeAssistantAdminAuthorizer:
    def __init__(
        self,
        *,
        client: HomeAssistantUserDirectoryClient | None = None,
        cache_ttl_s: float = 60.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if cache_ttl_s <= 0:
            raise ValueError("cache_ttl_s must be > 0")
        self.client = client or HomeAssistantUserDirectoryClient()
        self.cache_ttl_s = cache_ttl_s
        self.clock = clock
        self._cache: dict[str, _CachedAdminResult] = {}

    async def is_active_admin(self, user_id: str) -> bool:
        normalized_id = user_id.strip()
        if not normalized_id:
            raise ValueError("user_id is required")

        now = self.clock()
        cached = self._cache.get(normalized_id)
        if cached is not None and cached.expires_at > now:
            return cached.allowed

        user = await self.client.fetch_user(normalized_id)
        allowed = bool(
            user is not None
            and user.is_active
            and not user.system_generated
            and user.is_admin
        )
        self._cache[normalized_id] = _CachedAdminResult(
            allowed=allowed,
            expires_at=now + self.cache_ttl_s,
        )
        return allowed

    def clear(self, user_id: str | None = None) -> None:
        if user_id is None:
            self._cache.clear()
            return
        self._cache.pop(user_id.strip(), None)


def parse_user_list(payload: object) -> tuple[HomeAssistantUser, ...]:
    if not isinstance(payload, list):
        raise HomeAssistantConnectionError("config/auth/list returned an invalid payload")

    users: list[HomeAssistantUser] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        user_id = item.get("id")
        if not isinstance(user_id, str) or not user_id:
            continue
        raw_groups = item.get("group_ids")
        groups = (
            frozenset(group for group in raw_groups if isinstance(group, str))
            if isinstance(raw_groups, list)
            else frozenset()
        )
        users.append(
            HomeAssistantUser(
                user_id=user_id,
                username=_optional_string(item.get("username")),
                name=_optional_string(item.get("name")),
                is_owner=bool(item.get("is_owner", False)),
                is_active=bool(item.get("is_active", False)),
                system_generated=bool(item.get("system_generated", False)),
                group_ids=groups,
            )
        )
    return tuple(users)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None
