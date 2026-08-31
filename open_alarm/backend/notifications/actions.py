from __future__ import annotations

import asyncio
from collections.abc import Callable
from sqlite3 import Connection

from ..auth.models import UserRole
from ..auth.repository import get_user
from ..ha.event_client import HAEvent, HomeAssistantEventStreamClient
from ..runtime.commands import acknowledge_alarm
from ..runtime.controller import RuntimeController
from .constants import ACK_ACTION_PREFIX, MOBILE_NOTIFICATION_ACTION_EVENT


class NotificationActionListener:
    def __init__(
        self,
        connection: Connection,
        *,
        runtime_provider: Callable[[], RuntimeController | None],
        client: HomeAssistantEventStreamClient | None = None,
    ) -> None:
        self.connection = connection
        self.runtime_provider = runtime_provider
        self.client = client or HomeAssistantEventStreamClient()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="open-alarm-notification-actions")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    def handle_event(self, event: HAEvent) -> bool:
        action_raw = event.data.get("action")
        if not isinstance(action_raw, str) or not action_raw.startswith(ACK_ACTION_PREFIX):
            return False
        suffix = action_raw.removeprefix(ACK_ACTION_PREFIX)
        try:
            activation_event_id = int(suffix)
        except ValueError:
            return False
        if activation_event_id <= 0 or event.context_user_id is None:
            return False

        user = get_user(self.connection, event.context_user_id)
        if user is None or not user.has_role(UserRole.OPERATOR):
            return False

        row = self.connection.execute(
            """
            SELECT alarm_id
            FROM alarm_event
            WHERE event_id = ? AND event_type IN ('ACTIVATE','REACTIVATE')
            """,
            (activation_event_id,),
        ).fetchone()
        if row is None:
            return False
        alarm_id = str(row[0])

        latest = self.connection.execute(
            """
            SELECT MAX(event_id)
            FROM alarm_event
            WHERE alarm_id = ? AND event_type IN ('ACTIVATE','REACTIVATE')
            """,
            (alarm_id,),
        ).fetchone()
        if latest is None or latest[0] is None or int(latest[0]) != activation_event_id:
            return False

        state = self.connection.execute(
            """
            SELECT lifecycle, pending_origin
            FROM alarm_state
            WHERE alarm_id = ?
            """,
            (alarm_id,),
        ).fetchone()
        if state is None:
            return False
        lifecycle = str(state[0])
        pending_origin = None if state[1] is None else str(state[1])
        ackable = lifecycle in {"ACTIVE_UNACK", "RTN_UNACK"} or (
            lifecycle == "PENDING_OFF" and pending_origin == "ACTIVE_UNACK"
        )
        if not ackable:
            return False

        try:
            result = acknowledge_alarm(
                self.connection,
                self.runtime_provider(),
                alarm_id=alarm_id,
                user_id=user.user_id,
                now=event.time_fired,
            )
        except KeyError:
            return False
        return bool(result.events)

    async def _run(self) -> None:
        async for event in self.client.stream_events(MOBILE_NOTIFICATION_ACTION_EVENT):
            if self._stop.is_set():
                return
            self.handle_event(event)
