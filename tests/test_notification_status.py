from datetime import UTC, datetime
from pathlib import Path

from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.db.notification_outbox import enqueue_notification
from open_alarm.backend.notifications.status import notification_outbox_status


def test_notification_status_counts_due_and_failures(tmp_path: Path) -> None:
    connection = connect(tmp_path / "notifications.db")
    apply_migrations(connection)
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

    enqueue_notification(
        connection,
        dedupe_key="pending",
        alarm_id="A1",
        event_type="ACTIVATE",
        route_key="notify.mobile_app_phone",
        payload={"message": "Alarm"},
        now=now,
    )
    with connection:
        connection.execute(
            """
            INSERT INTO notification_outbox(
                dedupe_key, alarm_id, event_type, route_key, payload_json,
                status, attempts, available_at_utc, last_error, created_at_utc
            ) VALUES ('failed', 'A2', 'ACTIVATE', 'notify.mobile_app_phone', '{}',
                      'FAILED', 5, ?, 'service unavailable', ?)
            """,
            (now.isoformat(), now.isoformat()),
        )

    status = notification_outbox_status(connection, now=now)

    assert status["counts"] == {"PENDING": 1, "PROCESSING": 0, "SENT": 0, "FAILED": 1}
    assert status["pending_due"] == 1
    assert status["recent_failures"] == [
        {
            "outbox_id": 2,
            "alarm_id": "A2",
            "event_type": "ACTIVATE",
            "route_key": "notify.mobile_app_phone",
            "attempts": 5,
            "last_error": "service unavailable",
            "created_at": now.isoformat(),
        }
    ]
    connection.close()
