from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from ..db.alarm_control_repository import expire_shelves
from ..ha.client import HAConnectionStatus, HAStateUpdate, HomeAssistantWebSocketClient
from .dispatcher import AlarmDispatcher
from .system_alarms import SystemAlarmManager


@dataclass(slots=True)
class RuntimeStatus:
    running: bool = False
    connected: bool = False
    ha_version: str | None = None
    subscription_mode: str | None = None
    reason: str | None = None
    last_state_at: datetime | None = None
    last_tick_at: datetime | None = None


class RuntimeController:
    def __init__(
        self,
        dispatcher: AlarmDispatcher,
        *,
        client: HomeAssistantWebSocketClient | None = None,
        tick_interval_s: float = 1.0,
        system_alarms: SystemAlarmManager | None = None,
    ) -> None:
        if tick_interval_s <= 0:
            raise ValueError("tick_interval_s must be > 0")
        self.dispatcher = dispatcher
        self.client = client or HomeAssistantWebSocketClient()
        self.tick_interval_s = tick_interval_s
        self.system_alarms = system_alarms
        self.status = RuntimeStatus()
        self._stream_task: asyncio.Task[None] | None = None
        self._tick_task: asyncio.Task[None] | None = None

    @property
    def monitored_entity_ids(self) -> tuple[str, ...]:
        return self.dispatcher.monitored_entity_ids

    async def start(self) -> None:
        if self.status.running:
            return

        self.status.running = True
        self._tick_task = asyncio.create_task(self._tick_loop(), name="open-alarm-tick")

        if self.monitored_entity_ids:
            self._stream_task = asyncio.create_task(
                self._stream_loop(),
                name="open-alarm-ha-websocket",
            )
        else:
            self.status.reason = "Active configuration has no enabled Home Assistant entities"

    async def stop(self) -> None:
        tasks = [task for task in (self._stream_task, self._tick_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._stream_task = None
        self._tick_task = None
        self.status.running = False
        self.status.connected = False

    def handle_stream_item(self, item: HAConnectionStatus | HAStateUpdate) -> None:
        if isinstance(item, HAConnectionStatus):
            self.status.connected = item.connected
            self.status.ha_version = item.ha_version or self.status.ha_version
            self.status.subscription_mode = item.subscription_mode or self.status.subscription_mode
            self.status.reason = item.reason
            if self.system_alarms is not None:
                self.system_alarms.set_ha_connected(
                    item.connected,
                    reason=item.reason,
                    now=datetime.now(UTC),
                )
            return

        self.dispatcher.process_entity(item.state, now=item.state.observed_at)
        self.status.last_state_at = item.state.observed_at

    def tick_once(self, *, now: datetime | None = None) -> None:
        current_time = now or datetime.now(UTC)
        self.dispatcher.tick(now=current_time)
        if self.dispatcher.connection is not None:
            expire_shelves(self.dispatcher.connection, now=current_time)
        self.status.last_tick_at = current_time

    def status_payload(self) -> dict[str, object]:
        return {
            "running": self.status.running,
            "connected": self.status.connected,
            "ha_version": self.status.ha_version,
            "subscription_mode": self.status.subscription_mode,
            "reason": self.status.reason,
            "monitored_entities": len(self.monitored_entity_ids),
            "last_state_at": _isoformat(self.status.last_state_at),
            "last_tick_at": _isoformat(self.status.last_tick_at),
            "system_alarm_active": (
                False if self.system_alarms is None else self.system_alarms.active_or_pending
            ),
        }

    async def _stream_loop(self) -> None:
        async for item in self.client.stream_states(self.monitored_entity_ids):
            self.handle_stream_item(item)

        self.status.connected = False
        self.status.reason = "Home Assistant state stream ended"
        if self.system_alarms is not None:
            self.system_alarms.set_ha_connected(False, reason=self.status.reason)

    async def _tick_loop(self) -> None:
        while True:
            self.tick_once()
            await asyncio.sleep(self.tick_interval_s)


def _isoformat(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None
