from __future__ import annotations

import asyncio
import json
from collections import deque
from types import TracebackType
from typing import Any, Self

import pytest

import open_alarm.backend.ha.users as users_module
from open_alarm.backend.ha.client import HomeAssistantConnectionError
from open_alarm.backend.ha.users import (
    HomeAssistantAdminAuthorizer,
    HomeAssistantUser,
    HomeAssistantUserDirectoryClient,
    parse_user_list,
)


class FakeWebSocket:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = deque(json.dumps(message) for message in messages)
        self.sent: list[dict[str, Any]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def recv(self) -> str:
        return self.messages.popleft()

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


class MutableUserDirectory:
    def __init__(self, user: HomeAssistantUser | None) -> None:
        self.user = user
        self.calls = 0

    async def fetch_user(self, user_id: str) -> HomeAssistantUser | None:
        assert user_id == "ha-admin"
        self.calls += 1
        return self.user


def _directory_user(*, admin: bool) -> HomeAssistantUser:
    return HomeAssistantUser(
        user_id="ha-admin",
        username="admin",
        name="Admin",
        is_owner=admin,
        is_active=True,
        system_generated=False,
        group_ids=frozenset({"system-admin"} if admin else {"system-users"}),
    )


def test_fetch_user_uses_config_auth_list(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeWebSocket(
        [
            {"type": "auth_required", "ha_version": "2026.8.3"},
            {"type": "auth_ok", "ha_version": "2026.8.3"},
            {
                "id": 1,
                "type": "result",
                "success": True,
                "result": [
                    {
                        "id": "ha-admin",
                        "username": "admin",
                        "name": "Admin",
                        "is_owner": True,
                        "is_active": True,
                        "system_generated": False,
                        "group_ids": ["system-admin"],
                    },
                    {
                        "id": "ha-user",
                        "username": "user",
                        "name": "User",
                        "is_owner": False,
                        "is_active": True,
                        "system_generated": False,
                        "group_ids": ["system-users"],
                    },
                ],
            },
        ]
    )
    monkeypatch.setattr(users_module, "websocket_connect", lambda *args, **kwargs: fake)

    user = asyncio.run(HomeAssistantUserDirectoryClient(token="test-token").fetch_user("ha-admin"))

    assert user is not None
    assert user.is_admin is True
    assert user.is_active is True
    assert user.system_generated is False
    assert fake.sent[0] == {"type": "auth", "access_token": "test-token"}
    assert fake.sent[1] == {"id": 1, "type": "config/auth/list"}


def test_admin_authorizer_caches_then_rechecks_after_ttl() -> None:
    now = [100.0]
    directory = MutableUserDirectory(_directory_user(admin=True))
    authorizer = HomeAssistantAdminAuthorizer(
        client=directory,  # type: ignore[arg-type]
        cache_ttl_s=60.0,
        clock=lambda: now[0],
    )

    assert asyncio.run(authorizer.is_active_admin("ha-admin")) is True
    assert directory.calls == 1

    directory.user = _directory_user(admin=False)
    now[0] = 159.0
    assert asyncio.run(authorizer.is_active_admin("ha-admin")) is True
    assert directory.calls == 1

    now[0] = 161.0
    assert asyncio.run(authorizer.is_active_admin("ha-admin")) is False
    assert directory.calls == 2


def test_parse_user_list_skips_malformed_rows_and_marks_non_admin() -> None:
    users = parse_user_list(
        [
            {"id": None},
            {
                "id": "ha-user",
                "name": "User",
                "is_active": True,
                "system_generated": False,
                "group_ids": ["system-users", 123],
            },
        ]
    )

    assert len(users) == 1
    assert users[0].user_id == "ha-user"
    assert users[0].group_ids == frozenset({"system-users"})
    assert users[0].is_admin is False


def test_parse_user_list_rejects_invalid_payload() -> None:
    with pytest.raises(HomeAssistantConnectionError, match="invalid payload"):
        parse_user_list({"id": "not-a-list"})
