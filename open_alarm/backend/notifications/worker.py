from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from sqlite3 import Connection

from ..db.notification_outbox import (
    NotificationOutboxItem,
    claim_pending_notifications,
    discard_claimed_notification,
    mark_notification_failed,
    mark_notification_sent,
)

NotificationDispatcher = Callable[[NotificationOutboxItem], Awaitable[None]]
_EVENT_DEDUPE_PATTERN = re.compile(r"^alarm-event:(\d+):policy:")
_ACTIVATION_EVENTS = frozenset({"ACTIVATE", "REACTIVATE"})
_CANCELING_EVENTS = (
    "RETURN",
    "SHELVE",
    "SUPPRESS",
    "OUT_OF_SERVICE",
    "INHIBIT",
)
_CURRENT_ACTIVE_LIFECYCLES = (
    "ACTIVE_UNACK",
    "ACTIVE_ACK",
    "PENDING_OFF",
)


class PermanentNotificationError(RuntimeError):
    """A notification row cannot succeed by retrying unchanged."""


class NotificationOutboxWorker:
    def __init__(
        self,
        connection: Connection,
        dispatcher: NotificationDispatcher,
        *,
        poll_interval_s: float = 1.0,
        batch_size: int = 20,
        lease_timeout_s: float = 60.0,
        max_attempts: int = 5,
        retry_delay_s: float = 30.0,
    ) -> None:
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be > 0")
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        if lease_timeout_s <= 0:
            raise ValueError("lease_timeout_s must be > 0")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be > 0")
        if retry_delay_s < 0:
            raise ValueError("retry_delay_s must be >= 0")

        self.connection = connection
        self.dispatcher = dispatcher
        self.poll_interval_s = poll_interval_s
        self.batch_size = batch_size
        self.lease_timeout_s = lease_timeout_s
        self.max_attempts = max_attempts
        self.retry_delay_s = retry_delay_s
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="open-alarm-notification-outbox")

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

    async def process_once(self) -> int:
        items = claim_pending_notifications(
            self.connection,
            limit=self.batch_size,
            lease_timeout_s=self.lease_timeout_s,
        )
        for item in items:
            if not self._still_dispatchable(item):
                discard_claimed_notification(self.connection, item.outbox_id)
                continue
            try:
                await self.dispatcher(item)
            except asyncio.CancelledError:
                raise
            except PermanentNotificationError as exc:
                mark_notification_failed(
                    self.connection,
                    item.outbox_id,
                    error=str(exc),
                    max_attempts=1,
                    retry_delay_s=0,
                )
            except (RuntimeError, OSError, TimeoutError) as exc:
                mark_notification_failed(
                    self.connection,
                    item.outbox_id,
                    error=str(exc),
                    max_attempts=self.max_attempts,
                    retry_delay_s=self.retry_delay_s,
                )
            else:
                mark_notification_sent(self.connection, item.outbox_id)
        return len(items)

    def _still_dispatchable(self, item: NotificationOutboxItem) -> bool:
        """Revalidate canonical activation rows immediately before external I/O."""
        if item.event_type not in _ACTIVATION_EVENTS:
            return True
        match = _EVENT_DEDUPE_PATTERN.match(item.dedupe_key)
        if match is None:
            # Non-canonical/manual outbox rows retain generic worker behavior.
            return True

        source_event_id = int(match.group(1))
        source = self.connection.execute(
            """
            SELECT event_type
            FROM alarm_event
            WHERE event_id = ? AND alarm_id = ?
            """,
            (source_event_id, item.alarm_id),
        ).fetchone()
        if source is None or str(source[0]) != item.event_type:
            return False

        state = self.connection.execute(
            """
            SELECT
                lifecycle,
                returned_at_utc,
                shelved_until_utc,
                suppressed,
                inhibited,
                out_of_service
            FROM alarm_state
            WHERE alarm_id = ?
            """,
            (item.alarm_id,),
        ).fetchone()
        if state is None:
            return False
        lifecycle = str(state[0])
        returned_at = state[1]
        hidden = state[2] is not None or bool(state[3]) or bool(state[4]) or bool(state[5])
        if lifecycle not in _CURRENT_ACTIVE_LIFECYCLES or returned_at is not None or hidden:
            return False

        placeholders = ",".join("?" for _ in _CANCELING_EVENTS)
        later_cancel = self.connection.execute(
            f"""
            SELECT 1
            FROM alarm_event
            WHERE alarm_id = ?
              AND event_id > ?
              AND event_type IN ({placeholders})
            LIMIT 1
            """,
            (item.alarm_id, source_event_id, *_CANCELING_EVENTS),
        ).fetchone()
        return later_cancel is None

    async def _run(self) -> None:
        while not self._stop.is_set():
            processed = await self.process_once()
            if processed > 0:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
            except TimeoutError:
                pass
