from datetime import UTC, datetime, timedelta
from pathlib import Path

from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.db.notification_outbox import (
    claim_pending_notifications,
    enqueue_notification,
    mark_notification_failed,
    mark_notification_sent,
    retry_failed_notifications,
)


def test_outbox_enqueue_is_idempotent_by_dedupe_key(tmp_path: Path) -> None:
    connection = connect(tmp_path / "outbox.db")
    apply_migrations(connection)
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)

    first = enqueue_notification(
        connection,
        dedupe_key="TEMP_HI:ACTIVATE:mobile",
        alarm_id="TEMP_HI",
        revision_id="rev-1",
        event_type="ACTIVATE",
        route_key="mobile",
        payload={"message": "Temperature high"},
        now=now,
    )
    second = enqueue_notification(
        connection,
        dedupe_key="TEMP_HI:ACTIVATE:mobile",
        alarm_id="TEMP_HI",
        revision_id="rev-1",
        event_type="ACTIVATE",
        route_key="mobile",
        payload={"message": "different payload is ignored for duplicate"},
        now=now,
    )

    assert first == second
    row = connection.execute(
        "SELECT COUNT(*), payload_json FROM notification_outbox"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == '{"message":"Temperature high"}'
    connection.close()


def test_claim_marks_processing_and_increments_attempts(tmp_path: Path) -> None:
    connection = connect(tmp_path / "claim.db")
    apply_migrations(connection)
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    outbox_id = enqueue_notification(
        connection,
        dedupe_key="TEMP_HI:ACTIVATE:mobile",
        alarm_id="TEMP_HI",
        event_type="ACTIVATE",
        route_key="mobile",
        payload={"message": "Temperature high"},
        now=now,
    )

    items = claim_pending_notifications(connection, now=now)

    assert len(items) == 1
    assert items[0].outbox_id == outbox_id
    assert items[0].status == "PROCESSING"
    assert items[0].attempts == 1
    assert items[0].locked_at == now
    connection.close()


def test_failed_notification_retries_then_becomes_terminal(tmp_path: Path) -> None:
    connection = connect(tmp_path / "retry.db")
    apply_migrations(connection)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    outbox_id = enqueue_notification(
        connection,
        dedupe_key="TEMP_HI:ACTIVATE:mobile",
        alarm_id="TEMP_HI",
        event_type="ACTIVATE",
        route_key="mobile",
        payload={"message": "Temperature high"},
        now=start,
    )

    first = claim_pending_notifications(connection, now=start)[0]
    assert first.attempts == 1
    assert mark_notification_failed(
        connection,
        outbox_id,
        error="temporary failure",
        max_attempts=2,
        retry_delay_s=30,
        now=start,
    ) == "PENDING"
    assert claim_pending_notifications(connection, now=start + timedelta(seconds=29)) == ()

    second = claim_pending_notifications(connection, now=start + timedelta(seconds=30))[0]
    assert second.attempts == 2
    assert mark_notification_failed(
        connection,
        outbox_id,
        error="still failing",
        max_attempts=2,
        retry_delay_s=30,
        now=start + timedelta(seconds=30),
    ) == "FAILED"

    row = connection.execute(
        "SELECT status, attempts, locked_at_utc, last_error FROM notification_outbox WHERE outbox_id = ?",
        (outbox_id,),
    ).fetchone()
    assert row == ("FAILED", 2, None, "still failing")
    connection.close()


def test_admin_retry_returns_terminal_failure_to_pending(tmp_path: Path) -> None:
    connection = connect(tmp_path / "manual-retry.db")
    apply_migrations(connection)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    outbox_id = enqueue_notification(
        connection,
        dedupe_key="TEMP_HI:ACTIVATE:mobile",
        alarm_id="TEMP_HI",
        event_type="ACTIVATE",
        route_key="mobile",
        payload={"message": "Temperature high"},
        now=start,
    )
    claim_pending_notifications(connection, now=start)
    mark_notification_failed(
        connection,
        outbox_id,
        error="permanent so far",
        max_attempts=1,
        now=start,
    )

    retried = retry_failed_notifications(
        connection,
        outbox_ids=(outbox_id,),
        now=start + timedelta(minutes=5),
    )

    assert retried == (outbox_id,)
    row = connection.execute(
        """
        SELECT status, attempts, available_at_utc, locked_at_utc, sent_at_utc, last_error
        FROM notification_outbox
        WHERE outbox_id = ?
        """,
        (outbox_id,),
    ).fetchone()
    assert row == (
        "PENDING",
        0,
        (start + timedelta(minutes=5)).isoformat(),
        None,
        None,
        None,
    )
    assert claim_pending_notifications(
        connection,
        now=start + timedelta(minutes=5),
    )[0].outbox_id == outbox_id
    connection.close()


def test_stale_processing_lease_is_reclaimed(tmp_path: Path) -> None:
    connection = connect(tmp_path / "lease.db")
    apply_migrations(connection)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    outbox_id = enqueue_notification(
        connection,
        dedupe_key="TEMP_HI:ACTIVATE:mobile",
        alarm_id="TEMP_HI",
        event_type="ACTIVATE",
        route_key="mobile",
        payload={"message": "Temperature high"},
        now=start,
    )
    first = claim_pending_notifications(connection, lease_timeout_s=60, now=start)[0]
    assert first.attempts == 1

    reclaimed = claim_pending_notifications(
        connection,
        lease_timeout_s=60,
        now=start + timedelta(seconds=61),
    )

    assert len(reclaimed) == 1
    assert reclaimed[0].outbox_id == outbox_id
    assert reclaimed[0].attempts == 2
    connection.close()


def test_mark_sent_is_terminal_success(tmp_path: Path) -> None:
    connection = connect(tmp_path / "sent.db")
    apply_migrations(connection)
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    outbox_id = enqueue_notification(
        connection,
        dedupe_key="SYS:RETURN:mobile",
        alarm_id="SYS_HA_CONNECTION_LOST",
        origin="SYSTEM",
        event_type="RETURN",
        route_key="mobile",
        payload={"message": "Home Assistant connection restored"},
        now=now,
    )
    claim_pending_notifications(connection, now=now)

    mark_notification_sent(connection, outbox_id, now=now + timedelta(seconds=1))

    row = connection.execute(
        "SELECT status, sent_at_utc, locked_at_utc, last_error FROM notification_outbox WHERE outbox_id = ?",
        (outbox_id,),
    ).fetchone()
    assert row[0] == "SENT"
    assert row[1] == (now + timedelta(seconds=1)).isoformat()
    assert row[2:] == (None, None)
    assert claim_pending_notifications(connection, now=now + timedelta(hours=1)) == ()
    connection.close()
