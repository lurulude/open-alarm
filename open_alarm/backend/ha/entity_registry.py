from __future__ import annotations

from collections.abc import Iterable, Mapping

from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import WebSocketException

from .client import HomeAssistantConnectionError, HomeAssistantWebSocketClient

ENTITY_REGISTRY_DISPLAY_COMMAND = "config/entity_registry/list_for_display"
DEVICE_REGISTRY_LIST_COMMAND = "config/device_registry/list"
ENTITY_PREVIEW_LIMIT = 20


class HomeAssistantEntityRegistryClient(HomeAssistantWebSocketClient):
    async def fetch_entities_for_display(self) -> list[dict[str, str | None]]:
        try:
            async with websocket_connect(
                self.url,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                max_queue=1024,
            ) as websocket:
                await self._authenticate(websocket)

                entity_command_id = self._command_id()
                await self._send(
                    websocket,
                    {"id": entity_command_id, "type": ENTITY_REGISTRY_DISPLAY_COMMAND},
                )
                entity_result = await self._wait_for_result(websocket, entity_command_id, [])

                device_command_id = self._command_id()
                await self._send(
                    websocket,
                    {"id": device_command_id, "type": DEVICE_REGISTRY_LIST_COMMAND},
                )
                device_result = await self._wait_for_result(websocket, device_command_id, [])
        except HomeAssistantConnectionError:
            raise
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise HomeAssistantConnectionError(
                f"Home Assistant entity registry lookup failed: {exc}"
            ) from exc

        if not isinstance(entity_result, Mapping):
            raise HomeAssistantConnectionError(
                "Home Assistant entity registry display result was not an object"
            )
        entities = entity_result.get("entities")
        if not isinstance(entities, list):
            raise HomeAssistantConnectionError(
                "Home Assistant entity registry display result did not contain an entity list"
            )
        if not isinstance(device_result, list):
            raise HomeAssistantConnectionError(
                "Home Assistant device registry result was not a list"
            )

        devices: dict[str, Mapping[str, object]] = {}
        for item in device_result:
            if not isinstance(item, Mapping):
                continue
            device_id = item.get("id")
            if isinstance(device_id, str) and device_id:
                devices[device_id] = item

        options: list[dict[str, str | None]] = []
        for item in entities:
            if not isinstance(item, Mapping):
                continue
            entity_id = item.get("ei")
            if not isinstance(entity_id, str) or not entity_id:
                continue
            name = item.get("en")
            platform = item.get("pl")
            device_id = item.get("di")
            device = devices.get(device_id) if isinstance(device_id, str) else None
            options.append(
                {
                    "entity_id": entity_id,
                    "name": name if isinstance(name, str) and name else None,
                    "platform": platform if isinstance(platform, str) and platform else None,
                    "device_name": _device_text(device, "name_by_user", "name"),
                    "manufacturer": _device_text(device, "manufacturer"),
                    "model": _device_text(device, "model"),
                }
            )

        return sorted(options, key=lambda option: str(option["entity_id"]))

    async def fetch_entity_state_previews(
        self,
        entity_ids: Iterable[str],
    ) -> dict[str, dict[str, str | None]]:
        monitored = sorted({entity_id for entity_id in entity_ids if entity_id})
        if not monitored:
            return {}
        if len(monitored) > ENTITY_PREVIEW_LIMIT:
            raise ValueError(f"at most {ENTITY_PREVIEW_LIMIT} entities can be previewed")

        try:
            async with websocket_connect(
                self.url,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                max_queue=1024,
            ) as websocket:
                await self._authenticate(websocket)
                subscription_id = self._command_id()
                await self._send(
                    websocket,
                    {
                        "id": subscription_id,
                        "type": "subscribe_entities",
                        "entity_ids": monitored,
                    },
                )
                buffered_events: list[Mapping[str, object]] = []
                await self._wait_for_result(websocket, subscription_id, buffered_events)
                message = next(
                    (
                        event
                        for event in buffered_events
                        if event.get("type") == "event" and event.get("id") == subscription_id
                    ),
                    None,
                )
                if message is None:
                    message = await self._wait_for_subscription_event(websocket, subscription_id)
        except HomeAssistantConnectionError:
            raise
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise HomeAssistantConnectionError(
                f"Home Assistant entity value lookup failed: {exc}"
            ) from exc

        event = message.get("event")
        if not isinstance(event, Mapping):
            return {}
        additions = event.get("a")
        if not isinstance(additions, Mapping):
            return {}

        previews: dict[str, dict[str, str | None]] = {}
        for entity_id, payload in additions.items():
            if not isinstance(entity_id, str) or not isinstance(payload, Mapping):
                continue
            attributes = payload.get("a")
            attrs = attributes if isinstance(attributes, Mapping) else {}
            previews[entity_id] = {
                "entity_id": entity_id,
                "state": _string_or_none(payload.get("s")),
                "friendly_name": _string_or_none(attrs.get("friendly_name")),
                "unit": _string_or_none(attrs.get("unit_of_measurement")),
                "device_class": _string_or_none(attrs.get("device_class")),
            }
        return previews


def _device_text(device: Mapping[str, object] | None, *keys: str) -> str | None:
    if device is None:
        return None
    for key in keys:
        value = device.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
