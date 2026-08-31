from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import WebSocketException

from .client import (
    DEFAULT_SUPERVISOR_WS_URL,
    HomeAssistantAuthError,
    HomeAssistantCommandError,
    HomeAssistantConnectionError,
    decode_message,
)


@dataclass(frozen=True, slots=True)
class HAEvent:
    event_type: str
    data: Mapping[str, Any]
    time_fired: datetime
    context_user_id: str | None = None


class HomeAssistantEventStreamClient:
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

    async def stream_events(self, event_type: str) -> AsyncIterator[HAEvent]:
        normalized = event_type.strip()
        if not normalized:
            raise ValueError("event_type is required")

        while True:
            try:
                async for event in self._stream_session(normalized):
                    yield event
            except asyncio.CancelledError:
                raise
            except (HomeAssistantConnectionError, OSError, TimeoutError, WebSocketException):
                pass

            if self.reconnect_delay_s > 0:
                await asyncio.sleep(self.reconnect_delay_s)

    async def _stream_session(self, event_type: str) -> AsyncIterator[HAEvent]:
        async with websocket_connect(
            self.url,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_queue=256,
        ) as websocket:
            await self._authenticate(websocket)
            subscription_id = self._command_id()
            await websocket.send(
                json.dumps(
                    {
                        "id": subscription_id,
                        "type": "subscribe_events",
                        "event_type": event_type,
                    },
                    separators=(",", ":"),
                )
            )
            await self._wait_for_result(websocket, subscription_id)

            async for raw_message in websocket:
                message = decode_message(raw_message)
                event = event_from_subscription_message(
                    message,
                    subscription_id=subscription_id,
                    expected_event_type=event_type,
                )
                if event is not None:
                    yield event

    async def _authenticate(self, websocket: Any) -> None:
        required = decode_message(await websocket.recv())
        if required.get("type") != "auth_required":
            raise HomeAssistantConnectionError("unexpected WebSocket authentication handshake")

        await websocket.send(
            json.dumps(
                {"type": "auth", "access_token": self._resolve_token()},
                separators=(",", ":"),
            )
        )
        response = decode_message(await websocket.recv())
        if response.get("type") == "auth_invalid":
            raise HomeAssistantAuthError(str(response.get("message") or "authentication failed"))
        if response.get("type") != "auth_ok":
            raise HomeAssistantConnectionError("Home Assistant did not complete authentication")

    @staticmethod
    async def _wait_for_result(websocket: Any, command_id: int) -> None:
        while True:
            message = decode_message(await websocket.recv())
            if message.get("type") != "result" or message.get("id") != command_id:
                continue
            if not message.get("success", False):
                error = message.get("error")
                code = str(error.get("code")) if isinstance(error, Mapping) and error.get("code") else None
                detail = error.get("message") if isinstance(error, Mapping) else error
                raise HomeAssistantCommandError(
                    f"Home Assistant command failed: {detail}",
                    code=code,
                )
            return


def event_from_subscription_message(
    message: Mapping[str, Any],
    *,
    subscription_id: int,
    expected_event_type: str,
) -> HAEvent | None:
    if message.get("type") != "event" or message.get("id") != subscription_id:
        return None
    event = message.get("event")
    if not isinstance(event, Mapping) or event.get("event_type") != expected_event_type:
        return None
    data = event.get("data")
    if not isinstance(data, Mapping):
        data = {}
    context = event.get("context")
    user_id_raw = context.get("user_id") if isinstance(context, Mapping) else None
    time_raw = event.get("time_fired")
    try:
        time_fired = datetime.fromisoformat(str(time_raw)) if time_raw else datetime.now(UTC)
    except ValueError:
        time_fired = datetime.now(UTC)
    if time_fired.tzinfo is None:
        time_fired = time_fired.replace(tzinfo=UTC)
    else:
        time_fired = time_fired.astimezone(UTC)
    return HAEvent(
        event_type=expected_event_type,
        data=data,
        time_fired=time_fired,
        context_user_id=str(user_id_raw) if user_id_raw is not None else None,
    )
