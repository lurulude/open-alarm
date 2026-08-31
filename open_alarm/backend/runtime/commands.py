from __future__ import annotations

from datetime import UTC, datetime
from sqlite3 import Connection

from ..domain.engine import EngineResult
from .controller import RuntimeController
from .system_alarms import SYSTEM_ALARM_DEFINITIONS, SystemAlarmManager


def acknowledge_alarm(
    connection: Connection,
    runtime: RuntimeController | None,
    *,
    alarm_id: str,
    user_id: str,
    now: datetime | None = None,
) -> EngineResult:
    timestamp = now or datetime.now(UTC)
    if alarm_id in SYSTEM_ALARM_DEFINITIONS:
        manager = (
            runtime.system_alarms
            if runtime is not None and runtime.system_alarms is not None
            else SystemAlarmManager(connection)
        )
        return manager.acknowledge(alarm_id, user_id=user_id, now=timestamp)

    if runtime is None:
        raise KeyError(alarm_id)
    return runtime.dispatcher.acknowledge(alarm_id, user_id=user_id, now=timestamp)


def reset_alarm(
    connection: Connection,
    runtime: RuntimeController | None,
    *,
    alarm_id: str,
    user_id: str,
    now: datetime | None = None,
) -> EngineResult:
    del connection
    if alarm_id in SYSTEM_ALARM_DEFINITIONS:
        raise ValueError("system alarm is not configured as latching")
    if runtime is None:
        raise KeyError(alarm_id)
    timestamp = now or datetime.now(UTC)
    return runtime.dispatcher.reset(alarm_id, user_id=user_id, now=timestamp)


def acknowledge_all(
    connection: Connection,
    runtime: RuntimeController | None,
    *,
    user_id: str,
    now: datetime | None = None,
) -> tuple[str, ...]:
    timestamp = now or datetime.now(UTC)
    rows = connection.execute(
        """
        SELECT alarm_id
        FROM alarm_state
        WHERE lifecycle IN ('ACTIVE_UNACK','RTN_UNACK')
           OR (lifecycle = 'PENDING_OFF' AND pending_origin = 'ACTIVE_UNACK')
        ORDER BY alarm_id
        """
    ).fetchall()

    acknowledged: list[str] = []
    for row in rows:
        alarm_id = str(row[0])
        try:
            result = acknowledge_alarm(
                connection,
                runtime,
                alarm_id=alarm_id,
                user_id=user_id,
                now=timestamp,
            )
        except KeyError:
            continue
        if result.events:
            acknowledged.append(alarm_id)
    return tuple(acknowledged)
