import asyncio
import json
from collections import deque
from types import TracebackType
from typing import Any, Self

import open_alarm.backend.ha.client as client_module
from open_alarm.backend.ha.client import HomeAssistantWebSocketClient


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


def test_call_service_uses_home_assistant_websocket_command(monkeypatch: Any) -> None:
    fake = FakeWebSocket(
        [
            {"type": "auth_required", "ha_version": "2026.8.3"},
            {"type": "auth_ok", "ha_version": "2026.8.3"},
            {
                "id": 1,
                "type": "result",
                "success": True,
                "result": {"context": {"id": "ctx-1"}, "response": None},
            },
        ]
    )
    monkeypatch.setattr(client_module, "websocket_connect", lambda *args, **kwargs: fake)
    client = HomeAssistantWebSocketClient(token="test-token")

    result = asyncio.run(
        client.call_service(
            "notify",
            "mobile_app_phone",
            service_data={
                "title": "Open Alarm",
                "message": "Temperature high",
                "data": {"tag": "TEMP_HI"},
            },
        )
    )

    assert result["context"]["id"] == "ctx-1"
    assert fake.sent == [
        {"type": "auth", "access_token": "test-token"},
        {
            "id": 1,
            "type": "call_service",
            "domain": "notify",
            "service": "mobile_app_phone",
            "service_data": {
                "title": "Open Alarm",
                "message": "Temperature high",
                "data": {"tag": "TEMP_HI"},
            },
        },
    ]


def test_call_service_supports_target_and_response(monkeypatch: Any) -> None:
    fake = FakeWebSocket(
        [
            {"type": "auth_required", "ha_version": "2026.8.3"},
            {"type": "auth_ok", "ha_version": "2026.8.3"},
            {"id": 1, "type": "result", "success": True, "result": {"response": {"ok": True}}},
        ]
    )
    monkeypatch.setattr(client_module, "websocket_connect", lambda *args, **kwargs: fake)
    client = HomeAssistantWebSocketClient(token="test-token")

    result = asyncio.run(
        client.call_service(
            "script",
            "turn_on",
            target={"entity_id": "script.open_alarm_route"},
            return_response=True,
        )
    )

    assert result["response"] == {"ok": True}
    assert fake.sent[1] == {
        "id": 1,
        "type": "call_service",
        "domain": "script",
        "service": "turn_on",
        "target": {"entity_id": "script.open_alarm_route"},
        "return_response": True,
    }
