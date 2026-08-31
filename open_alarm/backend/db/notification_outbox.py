from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection
from typing import Any


@dataclass(frozen=True, slots=True)
class NotificationOutboxItem:
    outbox_id: int
    dedupe_key: str
    alarm_id: str
    revision_id: str | None
    origin: str
    event_type: str
    route_key: str
    payload: dict[str, Any]
    status: str
    attempts: int
    available_at: datetime
    locked_at: datetime | None
    created_at: datetime


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None:
        return result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def enqueue_notification(
    connection: Connection,
    *,
    dedupe_key: str,
    alarm_id: str,
    event_type: str,
    route_key: str,
    payload: dict[str, Any],
    revision_id: str | None = None,
    origin: str = "ENGINEERING",
    available_at: datetime | None = None,
    now: datetime | None = None,
) -> int:
    with connection:
        return enqueue_notification_in_transaction(
            connection,
            dedupe_key=dedupe_key,
            alarm_id=alarm_id,
            event_type=event_type,
            route_key=route_key,
            payload=payload,
            revision_id=revision_id,
            origin=origin,
            available_at=available_at,
            now=now,
        )


def enqueue_notification_in_transaction(
    connection: Connection,
    *,
    dedupe_key: str,
    alarm_id: str,
    event_type: str,
    route_key: str,
    payload: dict[str, Any],
    revision_id: str | None = None,
    origin: str = "ENGINEERING",
    available_at: datetime | None = None,
    now: datetime | None = None,
) -> int:
    """Insert an outbox row without committing the caller's transaction."""
    if not dedupe_key.strip():
        raise ValueError("dedupe_key is required")
    if not alarm_id.strip():
        raise ValueError("alarm_id is required")
    if not event_type.strip():
        raise ValueError("event_type is required")
    if not route_key.strip():
        raise ValueError("route_key is required")
    normalized_origin = origin.strip().upper()
    if normalized_origin not in {"ENGINEERING", "SYSTEM"}:
        raise ValueError("origin must be ENGINEERING or SYSTEM")

    timestamp = _utc(now)
    due_at = _utc(available_at) if available_at is not None else timestamp
    payload_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    connection.execute(
        """
        INSERT INTO notification_outbox(
            dedupe_key,
            alarm_id,
            revision_id,
            origin,
            event_type,
            route_key,
            payload_json,
            status,
            attempts,
            available_at_utc,
            created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?)
        ON CONFLICT(dedupe_key) DO NOTHING
        """,
        (
            dedupe_key.strip(),
            alarm_id.strip(),
            revision_id,
            normalized_origin,
            event_type.strip(),
            route_key.strip(),
            payload_json,
            _iso(due_at),
            _iso(timestamp),
        ),
    )
    row = connection.execute(
        "SELECT outbox_id FROM notification_outbox WHERE dedupe_key = ?",
        (dedupe_key.strip(),),
    ).fetchone()
    if row is None:
        raise RuntimeError("notification outbox row was not persisted")
    return int(row[0])


def cancel_pending_activation_notifications(
    connection: Connection,
    *,
    alarm_id: str,
    revision_id: str | None = None,
    route_key: str | None = None,
) -> int:
    """Delete only unsent delayed activation rows, optionally scoped to revision/route."""
    clauses = [
        "alarm_id = ?",
        "event_type IN ('ACTIVATE', 'REACTIVATE')",
        "status = 'PENDING'",
    ]
    parameters: list[object] = [alarm_id]
    if revision_id is not None:
        clauses.append("revision_id = ?")
        parameters.append(revision_id)
    if route_key is not None:
        clauses.append("route_key = ?")
        parameters.append(route_key)

    cursor = connection.execute(
        f"DELETE FROM notification_outbox WHERE {' AND '.join(clauses)}",
        tuple(parameters),
    )
    return cursor.rowcount


def discard_claimed_notification(connection: Connection, outbox_id: int) -> bool:
    """Delete a claimed row that became obsolete before external dispatch."""
    with connection:
        cursor = connection.execute(
            "DELETE FROM notification_outbox WHERE outbox_id = ? AND status = 'PROCESSING'",
            (outbox_id,),
        )
    return cursor.rowcount == 1


def claim_pending_notifications(
    connection: Connection,
    *,
    limit: int = 20,
    lease_timeout_s: float = 60.0,
    now: datetime | None = None,
) -> tuple[NotificationOutboxItem, ...]:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if lease_timeout_s <= 0:
        raise ValueError("lease_timeout_s must be > 0")

    timestamp = _utc(now)
    stale_before = timestamp - timedelta(seconds=lease_timeout_s)

    with connection:
        connection.execute(
            """
            UPDATE notification_outbox
            SET status = 'PENDING', locked_at_utc = NULL
            WHERE status = 'PROCESSING'
              AND locked_at_utc IS NOT NULL
              AND locked_at_utc <= ?
            """,
            (_iso(stale_before),),
        )
        rows = connection.execute(
            """
            SELECT outbox_id
            FROM notification_outbox
            WHERE status = 'PENDING'
              AND available_at_utc <= ?
            ORDER BY outbox_id
            LIMIT ?
            """,
            (_iso(timestamp), limit),
        ).fetchall()
        ids = [int(row[0]) for row in rows]
        if not ids:
            return ()

        placeholders = ",".join("?" for _ in ids)
        connection.execute(
            f"""
            UPDATE notification_outbox
            SET status = 'PROCESSING',
                attempts = attempts + 1,
                locked_at_utc = ?
            WHERE outbox_id IN ({placeholders})
              AND status = 'PENDING'
            """,
            (_iso(timestamp), *ids),
        )
        claimed = connection.execute(
            f"""
            SELECT
                outbox_id,
                dedupe_key,
                alarm_id,
                revision_id,
                origin,
                event_type,
                route_key,
                payload_json,
                status,
                attempts,
                available_at_utc,
                locked_at_utc,
                created_at_utc
            FROM notification_outbox
            WHERE outbox_id IN ({placeholders})
              AND status = 'PROCESSING'
              AND locked_at_utc = ?
            ORDER BY outbox_id
            """,
            (*ids, _iso(timestamp)),
        ).fetchall()

    return tuple(_item(row) for row in claimed)


def mark_notification_sent(
    connection: Connection,
    outbox_id: int,
    *,
    now: datetime | None = None,
) -> None:
    timestamp = _utc(now)
    with connection:
        cursor = connection.execute(
            """
            UPDATE notification_outbox
            SET status = 'SENT', sent_at_utc = ?, locked_at_utc = NULL, last_error = NULL
            WHERE outbox_id = ? AND status = 'PROCESSING'
            """,
            (_iso(timestamp), outbox_id),
        )
    if cursor.rowcount != 1:
        raise KeyError(outbox_id)


def mark_notification_failed(
    connection: Connection,
    outbox_id: int,
    *,
    error: str,
    max_attempts: int = 5,
    retry_delay_s: float = 30.0,
    now: datetime | None = None,
) -> str:
    if max_attempts <= 0:
        raise ValueError("max_attempts must be > 0")
    if retry_delay_s < 0:
        raise ValueError("retry_delay_s must be >= 0")

    timestamp = _utc(now)
    row = connection.execute(
        "SELECT attempts, status FROM notification_outbox WHERE outbox_id = ?",
        (outbox_id,),
    ).fetchone()
    if row is None or str(row[1]) != "PROCESSING":
        raise KeyError(outbox_id)

    attempts = int(row[0])
    terminal = attempts >= max_attempts
    next_status = "FAILED" if terminal else "PENDING"
    next_available = timestamp if terminal else timestamp + timedelta(seconds=retry_delay_s)

    with connection:
        connection.execute(
            """
            UPDATE notification_outbox
            SET status = ?,
                available_at_utc = ?,
                locked_at_utc = NULL,
                last_error = ?
            WHERE outbox_id = ? AND status = 'PROCESSING'
            """,
            (next_status, _iso(next_available), error[:4000], outbox_id),
        )
    return next_status


def retry_failed_notifications(
    connection: Connection,
    *,
    outbox_ids: tuple[int, ...] | None = None,
    now: datetime | None = None,
) -> tuple[int, ...]:
    """Return terminal failures to the queue for an explicit operator retry."""
    timestamp = _iso(_utc(now))
    clauses = ["status = 'FAILED'"]
    parameters: list[object] = []
    if outbox_ids is not None:
        normalized_ids = tuple(sorted(set(outbox_ids)))
        if not normalized_ids:
            return ()
        placeholders = ",".join("?" for _ in normalized_ids)
        clauses.append(f"outbox_id IN ({placeholders})")
        parameters.extend(normalized_ids)

    rows = connection.execute(
        f"SELECT outbox_id FROM notification_outbox WHERE {' AND '.join(clauses)} ORDER BY outbox_id",
        tuple(parameters),
    ).fetchall()
    retry_ids = tuple(int(row[0]) for row in rows)
    if not retry_ids:
        return ()

    placeholders = ",".join("?" for _ in retry_ids)
    with connection:
        connection.execute(
            f"""
            UPDATE notification_outbox
            SET status = 'PENDING',
                attempts = 0,
                available_at_utc = ?,
                locked_at_utc = NULL,
                sent_at_utc = NULL,
                last_error = NULL
            WHERE outbox_id IN ({placeholders})
              AND status = 'FAILED'
            """,
            (timestamp, *retry_ids),
        )
    return retry_ids


def _item(row: tuple[Any, ...]) -> NotificationOutboxItem:
    payload = json.loads(str(row[7]))
    if not isinstance(payload, dict):
        raise TypeError("notification outbox payload must be an object")
    available_at = _datetime(str(row[10]))
    created_at = _datetime(str(row[12]))
    if available_at is None or created_at is None:
        raise ValueError("notification outbox timestamps are required")
    return NotificationOutboxItem(
        outbox_id=int(row[0]),
        dedupe_key=str(row[1]),
        alarm_id=str(row[2]),
        revision_id=None if row[3] is None else str(row[3]),
        origin=str(row[4]),
        event_type=str(row[5]),
        route_key=str(row[6]),
        payload=payload,
        status=str(row[8]),
        attempts=int(row[9]),
        available_at=available_at,
        locked_at=_datetime(None if row[11] is None else str(row[11])),
        created_at=created_at,
    )
