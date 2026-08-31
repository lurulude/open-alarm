from __future__ import annotations

from datetime import UTC, datetime
from sqlite3 import Connection


def notification_outbox_status(connection: Connection, *, now: datetime | None = None) -> dict[str, object]:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    counts = {status: 0 for status in ("PENDING", "PROCESSING", "SENT", "FAILED")}
    for status, count in connection.execute(
        "SELECT status, COUNT(*) FROM notification_outbox GROUP BY status"
    ).fetchall():
        counts[str(status)] = int(count)

    pending_due = connection.execute(
        """
        SELECT COUNT(*)
        FROM notification_outbox
        WHERE status = 'PENDING' AND available_at_utc <= ?
        """,
        (timestamp,),
    ).fetchone()[0]

    failures = [
        {
            "outbox_id": int(row[0]),
            "alarm_id": str(row[1]),
            "event_type": str(row[2]),
            "route_key": str(row[3]),
            "attempts": int(row[4]),
            "last_error": None if row[5] is None else str(row[5]),
            "created_at": str(row[6]),
        }
        for row in connection.execute(
            """
            SELECT outbox_id, alarm_id, event_type, route_key, attempts, last_error, created_at_utc
            FROM notification_outbox
            WHERE status = 'FAILED'
            ORDER BY outbox_id DESC
            LIMIT 50
            """
        ).fetchall()
    ]

    return {
        "counts": counts,
        "pending_due": int(pending_due),
        "recent_failures": failures,
    }
