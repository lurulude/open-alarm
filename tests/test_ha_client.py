import asyncio
import json
from collections import deque
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self

import pytest

import open_alarm.backend.ha.client as client_module
from open_alarm.backend.ha.client import (
    HAConnectionStatus,
    HAStateUpdate,
    HomeAssistantConnectionError,
    HomeAssistantWebSocketClient,
)
from open_alarm.backend.ha.models import EntityQuality


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

    def __aiter__(self) -> "FakeWebSocket":
        return self

    async def __anext__(self) -> str:
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.popleft()


def test_fetch_states_authenticates_with_supervisor_token(monkeypatch: Any) -> None:
    fake = FakeWebSocket(
        [
            {"type": "auth_required", "ha_version": "2026.8.3"},
            {"type": "auth_ok", "ha_version": "2026.8.3"},
            {
                "id": 1,
                "type": "result",
                "success": True,
                "result": [{"entity_id": "sensor.temp", "state": "21.5", "attributes": {}}],
            },
        ]
    )
    monkeypatch.setattr(client_module, "websocket_connect", lambda *args, **kwargs: fake)
    client = HomeAssistantWebSocketClient(token="test-token")

    states = asyncio.run(client.fetch_states(["sensor.temp"]))

    assert states["sensor.temp"].state == "21.5"
    assert fake.sent[0] == {"type": "auth", "access_token": "test-token"}
    assert fake.sent[1]["type"] == "get_states"


def test_fetch_states_wraps_transport_failure(monkeypatch: Any) -> None:
    def fail_connect(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise OSError("connection refused")

    monkeypatch.setattr(client_module, "websocket_connect", fail_connect)
    client = HomeAssistantWebSocketClient(token="test-token")

    with pytest.raises(HomeAssistantConnectionError, match="connection refused"):
        asyncio.run(client.fetch_states())


def test_subscribe_entities_uses_filtered_snapshot_and_diffs(monkeypatch: Any) -> None:
    start = datetime(2026, 8, 30, 9, tzinfo=UTC)
    fake = FakeWebSocket(
        [
            {"type": "auth_required", "ha_version": "2026.8.3"},
            {"type": "auth_ok", "ha_version": "2026.8.3"},
            {"id": 1, "type": "result", "success": True, "result": None},
            {
                "id": 1,
                "type": "event",
                "event": {
                    "a": {
                        "sensor.temp": {
                            "s": "21.0",
                            "a": {"unit_of_measurement": "°C", "old": True},
                            "lc": start.timestamp(),
                            "lu": start.timestamp(),
                        }
                    }
                },
            },
            {
                "id": 1,
                "type": "event",
                "event": {
                    "c": {
                        "sensor.temp": {
                            "+": {
                                "s": "22.0",
                                "a": {"new": True},
                                "lc": (start + timedelta(seconds=1)).timestamp(),
                            },
                            "-": {"a": ["old"]},
                        }
                    }
                },
            },
        ]
    )
    monkeypatch.setattr(client_module, "websocket_connect", lambda *args, **kwargs: fake)
    client = HomeAssistantWebSocketClient(token="test-token")

    async def collect() -> tuple[HAConnectionStatus, HAStateUpdate, HAStateUpdate]:
        stream = client._stream_session(frozenset({"sensor.temp"}))
        status = await anext(stream)
        initial = await anext(stream)
        changed = await anext(stream)
        await stream.aclose()
        assert isinstance(status, HAConnectionStatus)
        assert isinstance(initial, HAStateUpdate)
        assert isinstance(changed, HAStateUpdate)
        return status, initial, changed

    status, initial, changed = asyncio.run(collect())

    assert status.subscription_mode == "subscribe_entities"
    assert initial.initial is True
    assert initial.state.state == "21.0"
    assert changed.initial is False
    assert changed.state.state == "22.0"
    assert changed.state.attributes == {"unit_of_measurement": "°C", "new": True}
    assert fake.sent[1] == {
        "id": 1,
        "type": "subscribe_entities",
        "entity_ids": ["sensor.temp"],
    }
    assert all(message["type"] != "get_states" for message in fake.sent[1:])


def test_subscribe_entities_removal_becomes_missing(monkeypatch: Any) -> None:
    start = datetime(2026, 8, 30, 9, tzinfo=UTC)
    fake = FakeWebSocket(
        [
            {"type": "auth_required", "ha_version": "2026.8.3"},
            {"type": "auth_ok", "ha_version": "2026.8.3"},
            {"id": 1, "type": "result", "success": True, "result": None},
            {
                "id": 1,
                "type": "event",
                "event": {
                    "a": {
                        "sensor.temp": {
                            "s": "21.0",
                            "a": {},
                            "lc": start.timestamp(),
                            "lu": start.timestamp(),
                        }
                    }
                },
            },
            {"id": 1, "type": "event", "event": {"r": ["sensor.temp"]}},
        ]
    )
    monkeypatch.setattr(client_module, "websocket_connect", lambda *args, **kwargs: fake)
    client = HomeAssistantWebSocketClient(token="test-token")

    async def collect_removed() -> HAStateUpdate:
        stream = client._stream_session(frozenset({"sensor.temp"}))
        await anext(stream)
        await anext(stream)
        removed = await anext(stream)
        await stream.aclose()
        assert isinstance(removed, HAStateUpdate)
        return removed

    removed = asyncio.run(collect_removed())
    assert removed.state.quality == EntityQuality.MISSING


def test_unsupported_subscribe_entities_falls_back_without_reconnect(monkeypatch: Any) -> None:
    old_time = datetime(2026, 8, 30, 9, tzinfo=UTC)
    new_time = old_time + timedelta(seconds=1)
    fake = FakeWebSocket(
        [
            {"type": "auth_required", "ha_version": "2021.12.0"},
            {"type": "auth_ok", "ha_version": "2021.12.0"},
            {
                "id": 1,
                "type": "result",
                "success": False,
                "error": {"code": "unknown_command", "message": "Unknown command."},
            },
            {"id": 2, "type": "result", "success": True, "result": None},
            {
                "id": 2,
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "time_fired": new_time.isoformat(),
                    "data": {
                        "entity_id": "sensor.temp",
                        "new_state": {
                            "entity_id": "sensor.temp",
                            "state": "22.0",
                            "attributes": {},
                            "last_updated": new_time.isoformat(),
                        },
                    },
                },
            },
            {
                "id": 3,
                "type": "result",
                "success": True,
                "result": [
                    {
                        "entity_id": "sensor.temp",
                        "state": "21.0",
                        "attributes": {},
                        "last_updated": old_time.isoformat(),
                    }
                ],
            },
        ]
    )
    monkeypatch.setattr(client_module, "websocket_connect", lambda *args, **kwargs: fake)
    client = HomeAssistantWebSocketClient(token="test-token")

    async def collect_initial() -> tuple[HAConnectionStatus, HAStateUpdate]:
        stream = client._stream_session(frozenset({"sensor.temp"}))
        status = await anext(stream)
        update = await anext(stream)
        await stream.aclose()
        assert isinstance(status, HAConnectionStatus)
        assert isinstance(update, HAStateUpdate)
        return status, update

    status, update = asyncio.run(collect_initial())

    assert status.connected is True
    assert status.subscription_mode == "state_changed"
    assert update.initial is True
    assert update.state.state == "22.0"
    assert update.state.quality == EntityQuality.GOOD
    assert [message["type"] for message in fake.sent[1:]] == [
        "subscribe_entities",
        "subscribe_events",
        "get_states",
    ]
