from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection

from ..domain.models import AlarmEventType
from .notification_outbox import cancel_pending_activation_notifications


class AlarmControlError(RuntimeError):
    pass


def _timestamp(value: datetime | None = None) -> datetime:
    return (value or datetime.now(UTC)).astimezone(UTC)


def _alarm_context(connection: Connection, alarm_id: str) -> tuple[str | None, str, str]:
    row = connection.execute(
        """
        SELECT s.revision_id, s.origin, COALESCE(c.message, '')
        FROM alarm_state s
        LEFT JOIN alarm_config c
          ON c.revision_id = s.revision_id AND c.alarm_id = s.alarm_id
        WHERE s.alarm_id = ?
        """,
        (alarm_id,),
    ).fetchone()
    if row is None:
        raise KeyError(alarm_id)
    return row[0], str(row[1]), str(row[2])


def _reject_system_hide(origin: str, action: str) -> None:
    if origin == "SYSTEM":
        raise AlarmControlError(f"built-in system alarms cannot be {action}")


def _clean_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    normalized = reason.strip()
    return normalized or None


def _insert_control_event(
    connection: Connection,
    *,
    alarm_id: str,
    revision_id: str | None,
    origin: str,
    event_type: AlarmEventType,
    user_id: str | None,
    message: str,
    at: datetime,
    details: dict[str, object] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO alarm_event(
            alarm_id, revision_id, origin, event_type, event_at_utc,
            user_id, value_json, message, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            alarm_id,
            revision_id,
            origin,
            event_type.value,
            at.isoformat(),
            user_id,
            message,
            None if details is None else json.dumps(details, separators=(",", ":"), ensure_ascii=False),
        ),
    )


def shelve_alarm(
    connection: Connection,
    alarm_id: str,
    *,
    duration_s: float,
    user_id: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> datetime:
    if duration_s <= 0:
        raise ValueError("duration_s must be > 0")
    if duration_s > 30 * 24 * 60 * 60:
        raise ValueError("duration_s must not exceed 30 days")
    at = _timestamp(now)
    until = at + timedelta(seconds=duration_s)
    revision_id, origin, message = _alarm_context(connection, alarm_id)
    _reject_system_hide(origin, "shelved")
    with connection:
        connection.execute(
            "UPDATE alarm_state SET shelved_until_utc = ?, updated_at_utc = ? WHERE alarm_id = ?",
            (until.isoformat(), at.isoformat(), alarm_id),
        )
        cancel_pending_activation_notifications(connection, alarm_id=alarm_id)
        _insert_control_event(
            connection,
            alarm_id=alarm_id,
            revision_id=revision_id,
            origin=origin,
            event_type=AlarmEventType.SHELVE,
            user_id=user_id,
            message=message,
            at=at,
            details={"shelved_until_utc": until.isoformat(), "reason": _clean_reason(reason)},
        )
    return until


def unshelve_alarm(
    connection: Connection,
    alarm_id: str,
    *,
    user_id: str | None,
    reason: str | None = None,
    now: datetime | None = None,
) -> bool:
    at = _timestamp(now)
    revision_id, origin, message = _alarm_context(connection, alarm_id)
    current = connection.execute(
        "SELECT shelved_until_utc FROM alarm_state WHERE alarm_id = ?",
        (alarm_id,),
    ).fetchone()
    if current is None or current[0] is None:
        return False
    with connection:
        connection.execute(
            "UPDATE alarm_state SET shelved_until_utc = NULL, updated_at_utc = ? WHERE alarm_id = ?",
            (at.isoformat(), alarm_id),
        )
        _insert_control_event(
            connection,
            alarm_id=alarm_id,
            revision_id=revision_id,
            origin=origin,
            event_type=AlarmEventType.UNSHELVE,
            user_id=user_id,
            message=message,
            at=at,
            details={"reason": _clean_reason(reason)},
        )
    return True


def set_suppressed(
    connection: Connection,
    alarm_id: str,
    *,
    suppressed: bool,
    user_id: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> bool:
    at = _timestamp(now)
    revision_id, origin, message = _alarm_context(connection, alarm_id)
    if suppressed:
        _reject_system_hide(origin, "suppressed")
    current = connection.execute(
        "SELECT suppressed FROM alarm_state WHERE alarm_id = ?",
        (alarm_id,),
    ).fetchone()
    if current is None:
        raise KeyError(alarm_id)
    if bool(current[0]) == suppressed:
        return False
    event_type = AlarmEventType.SUPPRESS if suppressed else AlarmEventType.UNSUPPRESS
    with connection:
        connection.execute(
            "UPDATE alarm_state SET suppressed = ?, updated_at_utc = ? WHERE alarm_id = ?",
            (int(suppressed), at.isoformat(), alarm_id),
        )
        if suppressed:
            cancel_pending_activation_notifications(connection, alarm_id=alarm_id)
        _insert_control_event(
            connection,
            alarm_id=alarm_id,
            revision_id=revision_id,
            origin=origin,
            event_type=event_type,
            user_id=user_id,
            message=message,
            at=at,
            details={"reason": _clean_reason(reason)},
        )
    return True


def set_automatic_inhibition(
    connection: Connection,
    alarm_id: str,
    *,
    inhibited_by: tuple[str, ...],
    now: datetime | None = None,
) -> bool:
    at = _timestamp(now)
    normalized = tuple(sorted(set(inhibited_by)))
    revision_id, origin, message = _alarm_context(connection, alarm_id)
    current = connection.execute(
        "SELECT inhibited, inhibited_by_json FROM alarm_state WHERE alarm_id = ?",
        (alarm_id,),
    ).fetchone()
    if current is None:
        raise KeyError(alarm_id)

    previous_ids = tuple(json.loads(current[1])) if current[1] else ()
    if bool(current[0]) == bool(normalized) and previous_ids == normalized:
        return False

    event_type = AlarmEventType.INHIBIT if normalized else AlarmEventType.UNINHIBIT
    with connection:
        connection.execute(
            """
            UPDATE alarm_state
            SET inhibited = ?, inhibited_by_json = ?, updated_at_utc = ?
            WHERE alarm_id = ?
            """,
            (
                int(bool(normalized)),
                json.dumps(normalized, separators=(",", ":")) if normalized else None,
                at.isoformat(),
                alarm_id,
            ),
        )
        if normalized:
            cancel_pending_activation_notifications(connection, alarm_id=alarm_id)
        _insert_control_event(
            connection,
            alarm_id=alarm_id,
            revision_id=revision_id,
            origin=origin,
            event_type=event_type,
            user_id=None,
            message=message,
            at=at,
            details={
                "inhibited_by_alarm_ids": list(normalized),
                "previous_inhibited_by_alarm_ids": list(previous_ids),
            },
        )
    return True


def set_out_of_service(
    connection: Connection,
    alarm_id: str,
    *,
    out_of_service: bool,
    user_id: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> bool:
    at = _timestamp(now)
    revision_id, origin, message = _alarm_context(connection, alarm_id)
    if out_of_service:
        _reject_system_hide(origin, "taken out of service")
    current = connection.execute(
        "SELECT out_of_service FROM alarm_state WHERE alarm_id = ?",
        (alarm_id,),
    ).fetchone()
    if current is None:
        raise KeyError(alarm_id)
    if bool(current[0]) == out_of_service:
        return False
    event_type = AlarmEventType.OUT_OF_SERVICE if out_of_service else AlarmEventType.IN_SERVICE
    with connection:
        connection.execute(
            "UPDATE alarm_state SET out_of_service = ?, updated_at_utc = ? WHERE alarm_id = ?",
            (int(out_of_service), at.isoformat(), alarm_id),
        )
        if out_of_service:
            cancel_pending_activation_notifications(connection, alarm_id=alarm_id)
        _insert_control_event(
            connection,
            alarm_id=alarm_id,
            revision_id=revision_id,
            origin=origin,
            event_type=event_type,
            user_id=user_id,
            message=message,
            at=at,
            details={"reason": _clean_reason(reason)},
        )
    return True


def expire_shelves(connection: Connection, *, now: datetime | None = None) -> tuple[str, ...]:
    at = _timestamp(now)
    alarm_ids = tuple(
        str(row[0])
        for row in connection.execute(
            """
            SELECT alarm_id
            FROM alarm_state
            WHERE shelved_until_utc IS NOT NULL AND shelved_until_utc <= ?
            ORDER BY alarm_id
            """,
            (at.isoformat(),),
        ).fetchall()
    )
    for alarm_id in alarm_ids:
        unshelve_alarm(
            connection,
            alarm_id,
            user_id=None,
            reason="EXPIRED",
            now=at,
        )
    return alarm_ids
