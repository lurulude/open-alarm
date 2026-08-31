from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import WebSocketException

from .models import HAEntityState, latest_state, normalize_entity_state, parse_ha_datetime

DEFAULT_SUPERVISOR_WS_URL = "ws://supervisor/core/websocket"
SUBSCRIPTION_ENTITIES = "subscribe_entities"
SUBSCRIPTION_STATE_CHANGED = "state_changed"


class HomeAssistantConnectionError(RuntimeError):
    pass


class HomeAssistantAuthError(HomeAssistantConnectionError):
    pass


class HomeAssistantCommandError(HomeAssistantConnectionError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class HAConnectionStatus:
    connected: bool
    ha_version: str | None = None
    reason: str | None = None
    subscription_mode: str | None = None


@dataclass(frozen=True, slots=True)
class HAStateUpdate:
    state: HAEntityState
    initial: bool = False


HAStreamItem = HAConnectionStatus | HAStateUpdate


class HomeAssistantWebSocketClient:
    def __init__(
        self,
        *,
        url: str = DEFAULT_SUPERVISOR_WS_URL,
        token: str | None = None,
        reconnect_delay_s: float = 5.0,
    ) -> None:
        if reconnect_delay_s < 0:
            raise ValueError("reconnect_delay_s must be >= 0")
        self.url = url
        self.token = token
        self.reconnect_delay_s = reconnect_delay_s
        self._next_command_id = 1

    def _resolve_token(self) -> str:
        token = self.token or os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise HomeAssistantAuthError("SUPERVISOR_TOKEN is not available")
        return token

    def _command_id(self) -> int:
        command_id = self._next_command_id
        self._next_command_id += 1
        return command_id

    async def fetch_states(
        self,
        entity_ids: Iterable[str] | None = None,
    ) -> dict[str, HAEntityState]:
        monitored = None if entity_ids is None else frozenset(entity_ids)
        try:
            async with websocket_connect(
                self.url,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                max_queue=1024,
            ) as websocket:
                await self._authenticate(websocket)
                command_id = self._command_id()
                await self._send(websocket, {"id": command_id, "type": "get_states"})
                result = await self._wait_for_result(websocket, command_id, [])
                return bootstrap_snapshot(result, monitored=monitored)
        except HomeAssistantConnectionError:
            raise
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise HomeAssistantConnectionError(
                f"Home Assistant WebSocket state lookup failed: {exc}"
            ) from exc

    async def call_service(
        self,
        domain: str,
        service: str,
        *,
        service_data: Mapping[str, Any] | None = None,
        target: Mapping[str, Any] | None = None,
        return_response: bool = False,
    ) -> Any:
        if not domain.strip() or not service.strip():
            raise ValueError("domain and service are required")

        async with websocket_connect(
            self.url,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_queue=1024,
        ) as websocket:
            await self._authenticate(websocket)
            command_id = self._command_id()
            payload: dict[str, Any] = {
                "id": command_id,
                "type": "call_service",
                "domain": domain.strip(),
                "service": service.strip(),
            }
            if service_data is not None:
                payload["service_data"] = dict(service_data)
            if target is not None:
                payload["target"] = dict(target)
            if return_response:
                payload["return_response"] = True
            await self._send(websocket, payload)
            return await self._wait_for_result(websocket, command_id, [])

    async def stream_states(self, entity_ids: Iterable[str]) -> AsyncIterator[HAStreamItem]:
        monitored = frozenset(entity_ids)
        if not monitored:
            raise ValueError("at least one entity_id is required")

        while True:
            try:
                async for item in self._stream_session(monitored):
                    yield item
                yield HAConnectionStatus(connected=False, reason="Home Assistant WebSocket closed")
            except asyncio.CancelledError:
                raise
            except (HomeAssistantConnectionError, OSError, TimeoutError, WebSocketException) as exc:
                yield HAConnectionStatus(connected=False, reason=str(exc))

            if self.reconnect_delay_s > 0:
                await asyncio.sleep(self.reconnect_delay_s)

    async def _stream_session(self, monitored: frozenset[str]) -> AsyncIterator[HAStreamItem]:
        async with websocket_connect(
            self.url,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_queue=1024,
        ) as websocket:
            ha_version = await self._authenticate(websocket)

            try:
                async for item in self._stream_subscribe_entities(websocket, monitored, ha_version):
                    yield item
            except HomeAssistantCommandError:
                async for item in self._stream_state_changed(websocket, monitored, ha_version):
                    yield item

    async def _stream_subscribe_entities(
        self,
        websocket: Any,
        monitored: frozenset[str],
        ha_version: str | None,
    ) -> AsyncIterator[HAStreamItem]:
        subscription_id = self._command_id()
        await self._send(
            websocket,
            {
                "id": subscription_id,
                "type": SUBSCRIPTION_ENTITIES,
                "entity_ids": sorted(monitored),
            },
        )
        await self._wait_for_result(websocket, subscription_id, [])

        message = await self._wait_for_subscription_event(websocket, subscription_id)
        states: dict[str, HAEntityState] = {}
        apply_subscribe_entities_event(states, message["event"], monitored=monitored)

        observed_at = datetime.now(UTC)
        for entity_id in monitored:
            states.setdefault(
                entity_id,
                normalize_entity_state(entity_id, None, observed_at=observed_at),
            )

        yield HAConnectionStatus(
            connected=True,
            ha_version=ha_version,
            subscription_mode=SUBSCRIPTION_ENTITIES,
        )
        for entity_id in sorted(monitored):
            yield HAStateUpdate(state=states[entity_id], initial=True)

        async for raw_message in websocket:
            message = decode_message(raw_message)
            if message.get("type") != "event" or message.get("id") != subscription_id:
                continue
            event = message.get("event")
            if not isinstance(event, Mapping):
                continue
            for update in apply_subscribe_entities_event(states, event, monitored=monitored):
                yield HAStateUpdate(state=update, initial=False)

    async def _stream_state_changed(
        self,
        websocket: Any,
        monitored: frozenset[str],
        ha_version: str | None,
    ) -> AsyncIterator[HAStreamItem]:
        buffered_events: list[Mapping[str, Any]] = []

        subscribe_id = self._command_id()
        await self._send(
            websocket,
            {"id": subscribe_id, "type": "subscribe_events", "event_type": "state_changed"},
        )
        await self._wait_for_result(websocket, subscribe_id, buffered_events)

        states_id = self._command_id()
        await self._send(websocket, {"id": states_id, "type": "get_states"})
        snapshot = await self._wait_for_result(websocket, states_id, buffered_events)

        states = bootstrap_snapshot(snapshot, monitored=monitored)
        for message in buffered_events:
            update = state_from_event_message(message)
            if update is not None and update.entity_id in monitored:
                states[update.entity_id] = latest_state(states.get(update.entity_id), update)

        observed_at = datetime.now(UTC)
        for entity_id in monitored:
            states.setdefault(
                entity_id,
                normalize_entity_state(entity_id, None, observed_at=observed_at),
            )

        yield HAConnectionStatus(
            connected=True,
            ha_version=ha_version,
            subscription_mode=SUBSCRIPTION_STATE_CHANGED,
        )
        for entity_id in sorted(monitored):
            yield HAStateUpdate(state=states[entity_id], initial=True)

        async for raw_message in websocket:
            message = decode_message(raw_message)
            update = state_from_event_message(message)
            if update is not None and update.entity_id in monitored:
                yield HAStateUpdate(state=update, initial=False)

    async def _authenticate(self, websocket: Any) -> str | None:
        required = decode_message(await websocket.recv())
        if required.get("type") != "auth_required":
            raise HomeAssistantAuthError("Home Assistant did not request authentication")
        await self._send(websocket, {"type": "auth", "access_token": self._resolve_token()})
        response = decode_message(await websocket.recv())
        if response.get("type") == "auth_invalid":
            raise HomeAssistantAuthError(str(response.get("message", "invalid Home Assistant token")))
        if response.get("type") != "auth_ok":
            raise HomeAssistantAuthError("Home Assistant authentication failed")
        version = response.get("ha_version") or required.get("ha_version")
        return None if version is None else str(version)

    async def _send(self, websocket: Any, payload: Mapping[str, Any]) -> None:
        await websocket.send(json.dumps(dict(payload), separators=(",", ":")))

    async def _wait_for_result(
        self,
        websocket: Any,
        command_id: int,
        buffered_events: list[Mapping[str, Any]],
    ) -> Any:
        while True:
            message = decode_message(await websocket.recv())
            if message.get("type") == "event":
                buffered_events.append(message)
                continue
            if message.get("type") != "result" or message.get("id") != command_id:
                continue
            if not message.get("success", False):
                error = message.get("error")
                if isinstance(error, Mapping):
                    raise HomeAssistantCommandError(
                        str(error.get("message", "Home Assistant command failed")),
                        code=None if error.get("code") is None else str(error.get("code")),
                    )
                raise HomeAssistantCommandError("Home Assistant command failed")
            return message.get("result")

    async def _wait_for_subscription_event(self, websocket: Any, subscription_id: int) -> Mapping[str, Any]:
        while True:
            message = decode_message(await websocket.recv())
            if message.get("type") == "event" and message.get("id") == subscription_id:
                return message


def decode_message(raw: Any) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        raise HomeAssistantConnectionError("Home Assistant WebSocket message was not text")
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HomeAssistantConnectionError("Home Assistant WebSocket message was invalid JSON") from exc
    if not isinstance(message, dict):
        raise HomeAssistantConnectionError("Home Assistant WebSocket message was not an object")
    return message


def bootstrap_snapshot(
    payload: Any,
    *,
    monitored: frozenset[str] | None,
) -> dict[str, HAEntityState]:
    if not isinstance(payload, list):
        raise HomeAssistantConnectionError("Home Assistant get_states result was not a list")
    observed_at = datetime.now(UTC)
    states: dict[str, HAEntityState] = {}
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        entity_id_raw = item.get("entity_id")
        if not isinstance(entity_id_raw, str):
            continue
        if monitored is not None and entity_id_raw not in monitored:
            continue
        states[entity_id_raw] = normalize_entity_state(
            entity_id_raw,
            item,
            observed_at=observed_at,
        )
    if monitored is not None:
        for entity_id in monitored:
            states.setdefault(
                entity_id,
                normalize_entity_state(entity_id, None, observed_at=observed_at),
            )
    return states


def state_from_event_message(message: Mapping[str, Any]) -> HAEntityState | None:
    if message.get("type") != "event":
        return None
    event = message.get("event")
    if not isinstance(event, Mapping):
        return None
    data = event.get("data")
    if not isinstance(data, Mapping):
        return None
    entity_id = data.get("entity_id")
    if not isinstance(entity_id, str):
        return None
    observed_at = parse_ha_datetime(event.get("time_fired")) or datetime.now(UTC)
    new_state = data.get("new_state")
    return normalize_entity_state(entity_id, new_state, observed_at=observed_at)


def apply_subscribe_entities_event(
    states: dict[str, HAEntityState],
    event: Mapping[str, Any],
    *,
    monitored: frozenset[str],
) -> list[HAEntityState]:
    observed_at = datetime.now(UTC)
    updates: list[HAEntityState] = []

    additions = event.get("a")
    if isinstance(additions, Mapping):
        for entity_id, payload in additions.items():
            if isinstance(entity_id, str) and entity_id in monitored and isinstance(payload, Mapping):
                update = normalize_compact_state(entity_id, payload, observed_at=observed_at)
                states[entity_id] = update
                updates.append(update)

    changes = event.get("c")
    if isinstance(changes, Mapping):
        for entity_id, change in changes.items():
            if not isinstance(entity_id, str) or entity_id not in monitored or not isinstance(change, Mapping):
                continue
            current = states.get(entity_id)
            updated = apply_compact_change(entity_id, current, change, observed_at=observed_at)
            states[entity_id] = updated
            updates.append(updated)

    removals = event.get("r")
    if isinstance(removals, list):
        for entity_id in removals:
            if not isinstance(entity_id, str) or entity_id not in monitored:
                continue
            missing = normalize_entity_state(entity_id, None, observed_at=observed_at)
            states[entity_id] = missing
            updates.append(missing)

    return updates


def normalize_compact_state(
    entity_id: str,
    payload: Mapping[str, Any],
    *,
    observed_at: datetime,
) -> HAEntityState:
    attributes = payload.get("a")
    full = {
        "state": payload.get("s"),
        "attributes": dict(attributes) if isinstance(attributes, Mapping) else {},
        "last_changed": _compact_time(payload.get("lc")),
        "last_updated": _compact_time(payload.get("lu")),
    }
    return normalize_entity_state(entity_id, full, observed_at=observed_at)


def apply_compact_change(
    entity_id: str,
    current: HAEntityState | None,
    change: Mapping[str, Any],
    *,
    observed_at: datetime,
) -> HAEntityState:
    if current is None:
        current = normalize_entity_state(entity_id, None, observed_at=observed_at)

    additions = change.get("+")
    removals = change.get("-")
    state = current.state
    attributes = dict(current.attributes)
    last_changed = current.last_changed
    last_updated = current.last_updated

    if isinstance(additions, Mapping):
        if "s" in additions:
            state = additions.get("s")
        if "lc" in additions:
            last_changed = parse_ha_datetime(_compact_time(additions.get("lc")))
        if "lu" in additions:
            last_updated = parse_ha_datetime(_compact_time(additions.get("lu")))
        attr_additions = additions.get("a")
        if isinstance(attr_additions, Mapping):
            attributes.update(attr_additions)

    if isinstance(removals, Mapping):
        attr_removals = removals.get("a")
        if isinstance(attr_removals, list):
            for key in attr_removals:
                if isinstance(key, str):
                    attributes.pop(key, None)

    full = {
        "state": state,
        "attributes": attributes,
        "last_changed": None if last_changed is None else last_changed.isoformat(),
        "last_updated": None if last_updated is None else last_updated.isoformat(),
    }
    return normalize_entity_state(entity_id, full, observed_at=observed_at)


def _compact_time(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC).isoformat()
    if isinstance(value, str):
        return value
    return None
