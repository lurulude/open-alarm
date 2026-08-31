from __future__ import annotations

import asyncio
from collections.abc import Callable
from sqlite3 import Connection

from ..db.alarm_browser import alarm_view_counts
from ..db.config_repository import load_active_compiled_config
from ..ha.client import HomeAssistantWebSocketClient
from ..ha.event_client import HomeAssistantEventStreamClient
from ..ha.state_publisher import HomeAssistantAlarmStatePublisher
from ..notifications.actions import NotificationActionListener
from ..notifications.ha_dispatcher import HomeAssistantNotificationDispatcher
from ..notifications.status import notification_outbox_status
from ..notifications.worker import NotificationDispatcher, NotificationOutboxWorker
from .controller import RuntimeController
from .dispatcher import AlarmDispatcher
from .system_alarms import (
    NOTIFICATION_DELIVERY_ALARM_ID,
    NOTIFICATION_WORKER_ALARM_ID,
    RUNTIME_CONFIG_ALARM_ID,
    SystemAlarmManager,
)


class RuntimeHost:
    def __init__(
        self,
        connection: Connection,
        *,
        client_factory: Callable[[], HomeAssistantWebSocketClient] = HomeAssistantWebSocketClient,
        event_client_factory: Callable[[], HomeAssistantEventStreamClient] = HomeAssistantEventStreamClient,
        notification_dispatcher: NotificationDispatcher | None = None,
        state_publisher: HomeAssistantAlarmStatePublisher | None = None,
        health_interval_s: float = 1.0,
    ) -> None:
        if health_interval_s <= 0:
            raise ValueError("health_interval_s must be > 0")
        self.connection = connection
        self.client_factory = client_factory
        self.health_interval_s = health_interval_s
        self.controller: RuntimeController | None = None
        self.system_alarms = SystemAlarmManager(connection)
        dispatcher = notification_dispatcher or HomeAssistantNotificationDispatcher(client_factory())
        self.notification_worker = NotificationOutboxWorker(connection, dispatcher)
        self.notification_action_listener = NotificationActionListener(
            connection,
            runtime_provider=lambda: self.controller,
            client=event_client_factory(),
        )
        self.state_publisher = state_publisher or HomeAssistantAlarmStatePublisher()
        self._health_task: asyncio.Task[None] | None = None
        self._config_error: str | None = None

    async def start(self) -> None:
        self.system_alarms.record_runtime_event("START")
        await self.notification_worker.start()
        await self.notification_action_listener.start()
        self._health_task = asyncio.create_task(self._health_loop(), name="open-alarm-health")
        try:
            await self.reload()
        except BaseException:
            await self._stop_health_task()
            await self.notification_action_listener.stop()
            await self.notification_worker.stop()
            raise

    async def stop(self) -> None:
        await self._stop_health_task()
        if self.controller is not None:
            await self.controller.stop()
            self.controller = None
        await self.notification_action_listener.stop()
        await self.notification_worker.stop()
        await self.state_publisher.publish_unavailable()
        self.system_alarms.record_runtime_event("STOP")

    async def reload(self) -> None:
        previous = self.controller
        try:
            active = load_active_compiled_config(self.connection)
            next_controller: RuntimeController | None = None
            if active is not None:
                revision_id, compiled = active
                next_controller = RuntimeController(
                    AlarmDispatcher(compiled, revision_id=revision_id, connection=self.connection),
                    client=self.client_factory(),
                    system_alarms=self.system_alarms,
                )
        except (KeyError, TypeError, ValueError) as exc:
            self.controller = None
            if previous is not None:
                await previous.stop()
            self._config_error = str(exc) or type(exc).__name__
            self.system_alarms.set_condition(
                RUNTIME_CONFIG_ALARM_ID,
                True,
                raw_value={"error": self._config_error},
            )
            self.system_alarms.record_runtime_event(
                "CONFIG_LOAD_FAILED",
                details={"error": self._config_error},
            )
            return

        self._config_error = None
        self.system_alarms.set_condition(
            RUNTIME_CONFIG_ALARM_ID,
            False,
            raw_value={"error": None},
        )
        self.controller = next_controller
        if previous is not None:
            await previous.stop()
        if next_controller is not None:
            await next_controller.start()

    def health_once(self) -> None:
        status = notification_outbox_status(self.connection)
        failed = int(status["counts"]["FAILED"])
        pending_due = int(status["pending_due"])
        self.system_alarms.set_condition(
            NOTIFICATION_WORKER_ALARM_ID,
            not self.notification_worker.running,
            raw_value={"running": self.notification_worker.running},
        )
        self.system_alarms.set_condition(
            NOTIFICATION_DELIVERY_ALARM_ID,
            failed > 0,
            raw_value={"failed": failed, "pending_due": pending_due},
        )
        self.system_alarms.tick()

    def status_payload(self) -> dict[str, object]:
        if self.controller is None:
            reason = (
                "Active configuration could not be loaded"
                if self._config_error is not None
                else "No active configuration revision"
            )
            return {
                "configured": False,
                "running": False,
                "connected": False,
                "reason": reason,
                "config_error": self._config_error,
                "monitored_entities": 0,
                "system_alarm_active": self.system_alarms.active_or_pending,
            }
        payload = self.controller.status_payload()
        payload["configured"] = True
        payload["active_revision_id"] = self.controller.dispatcher.revision_id
        payload["config_error"] = None
        return payload

    async def _health_loop(self) -> None:
        while True:
            self.health_once()
            unacknowledged = alarm_view_counts(self.connection)["unacknowledged"]
            await self.state_publisher.publish(unacknowledged)
            await asyncio.sleep(self.health_interval_s)

    async def _stop_health_task(self) -> None:
        task = self._health_task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._health_task = None
