import asyncio
import json
from collections import deque
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from fastapi.testclient import TestClient

import open_alarm.backend.ha.entity_registry as entity_registry_module
from open_alarm.backend.ha.entity_registry import HomeAssistantEntityRegistryClient

HEADERS = {
    "X-Remote-User-Id": "picker-admin",
    "X-Remote-User-Name": "picker-admin",
    "X-Remote-User-Display-Name": "Picker Admin",
}


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


def test_entity_picker_uses_compact_registry_and_device_context(monkeypatch: Any) -> None:
    fake = FakeWebSocket(
        [
            {"type": "auth_required", "ha_version": "2026.8.3"},
            {"type": "auth_ok", "ha_version": "2026.8.3"},
            {
                "id": 1,
                "type": "result",
                "success": True,
                "result": {
                    "entity_categories": {"0": "config", "1": "diagnostic"},
                    "entities": [
                        {
                            "ei": "sensor.temperature",
                            "pl": "mqtt",
                            "en": "Temperature",
                            "di": "device-1",
                        },
                        {"ei": "input_number.test_temperature", "pl": "input_number"},
                    ],
                },
            },
            {
                "id": 2,
                "type": "result",
                "success": True,
                "result": [
                    {
                        "id": "device-1",
                        "name": "Boiler controller",
                        "name_by_user": "Heating cabinet",
                        "manufacturer": "Acme",
                        "model": "T100",
                    }
                ],
            },
        ]
    )
    monkeypatch.setattr(entity_registry_module, "websocket_connect", lambda *args, **kwargs: fake)

    entities = asyncio.run(
        HomeAssistantEntityRegistryClient(token="test-token").fetch_entities_for_display()
    )

    assert entities == [
        {
            "entity_id": "input_number.test_temperature",
            "name": None,
            "platform": "input_number",
            "device_name": None,
            "manufacturer": None,
            "model": None,
        },
        {
            "entity_id": "sensor.temperature",
            "name": "Temperature",
            "platform": "mqtt",
            "device_name": "Heating cabinet",
            "manufacturer": "Acme",
            "model": "T100",
        },
    ]
    assert fake.sent[0] == {"type": "auth", "access_token": "test-token"}
    assert fake.sent[1] == {"id": 1, "type": "config/entity_registry/list_for_display"}
    assert fake.sent[2] == {"id": 2, "type": "config/device_registry/list"}
    assert all(message["type"] != "get_states" for message in fake.sent)


def test_entity_picker_fetches_only_requested_current_values(monkeypatch: Any) -> None:
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
                        "sensor.temperature": {
                            "s": "16.1",
                            "a": {
                                "friendly_name": "Container indoor temperature",
                                "unit_of_measurement": "°C",
                                "device_class": "temperature",
                            },
                        }
                    }
                },
            },
        ]
    )
    monkeypatch.setattr(entity_registry_module, "websocket_connect", lambda *args, **kwargs: fake)

    previews = asyncio.run(
        HomeAssistantEntityRegistryClient(token="test-token").fetch_entity_state_previews(
            ["sensor.temperature"]
        )
    )

    assert previews == {
        "sensor.temperature": {
            "entity_id": "sensor.temperature",
            "state": "16.1",
            "friendly_name": "Container indoor temperature",
            "unit": "°C",
            "device_class": "temperature",
        }
    }
    assert fake.sent[1] == {
        "id": 1,
        "type": "subscribe_entities",
        "entity_ids": ["sensor.temperature"],
    }
    assert all(message["type"] != "get_states" for message in fake.sent)


def test_engineering_entity_picker_api_returns_context_and_values(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("OPEN_ALARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPEN_ALARM_ENFORCE_INGRESS_SOURCE", "0")

    async def fetch_entities(self: HomeAssistantEntityRegistryClient) -> list[dict[str, str | None]]:
        del self
        return [
            {
                "entity_id": "input_number.test_temperature",
                "name": "Test temperature",
                "platform": "input_number",
                "device_name": "Test helpers",
                "manufacturer": None,
                "model": None,
            }
        ]

    async def fetch_values(
        self: HomeAssistantEntityRegistryClient,
        entity_ids: list[str],
    ) -> dict[str, dict[str, str | None]]:
        del self
        assert entity_ids == ["input_number.test_temperature"]
        return {
            "input_number.test_temperature": {
                "entity_id": "input_number.test_temperature",
                "state": "20",
                "friendly_name": "Test temperature",
                "unit": "°C",
                "device_class": None,
            }
        }

    monkeypatch.setattr(
        HomeAssistantEntityRegistryClient,
        "fetch_entities_for_display",
        fetch_entities,
    )
    monkeypatch.setattr(
        HomeAssistantEntityRegistryClient,
        "fetch_entity_state_previews",
        fetch_values,
    )

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        entities = client.get("/api/engineering/entities", headers=HEADERS)
        values = client.post(
            "/api/engineering/entity-values",
            headers=HEADERS,
            json={"entity_ids": ["input_number.test_temperature"]},
        )

    assert entities.status_code == 200
    assert entities.json() == [
        {
            "entity_id": "input_number.test_temperature",
            "name": "Test temperature",
            "platform": "input_number",
            "device_name": "Test helpers",
            "manufacturer": None,
            "model": None,
        }
    ]
    assert values.status_code == 200
    assert values.json()["input_number.test_temperature"]["state"] == "20"
