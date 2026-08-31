import asyncio
from datetime import UTC, datetime
from pathlib import Path

from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.db.notification_outbox import enqueue_notification
from open_alarm.backend.notifications.worker import (
    NotificationOutboxWorker,
    PermanentNotificationError,
)


def _canonical_activation(connection, *, alarm_id: str = "TEMP_HI") -> int:
    at = datetime(2020, 1, 1, tzinfo=UTC)
    with connection:
        cursor = connection.execute(
            """
            INSERT INTO alarm_event(
                alarm_id, revision_id, origin, event_type, event_at_utc, message
            ) VALUES (?, NULL, 'ENGINEERING', 'ACTIVATE', ?, 'Temperature high')
            """,
            (alarm_id, at.isoformat()),
        )
        source_event_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO alarm_state(
                alarm_id,
                revision_id,
                origin,
                lifecycle,
                condition_abnormal,
                active_since_utc,
                updated_at_utc
            ) VALUES (?, NULL, 'ENGINEERING', 'ACTIVE_UNACK', 1, ?, ?)
            """,
            (alarm_id, at.isoformat(), at.isoformat()),
        )
    enqueue_notification(
        connection,
        dedupe_key=f"alarm-event:{source_event_id}:policy:P1_PHONE",
        alarm_id=alarm_id,
        event_type="ACTIVATE",
        route_key="mobile",
        payload={"message": "Temperature high"},
        now=at,
    )
    return source_event_id


def _later_event(connection, alarm_id: str, event_type: str, second: int) -> int:
    at = datetime(2020, 1, 1, 0, 0, second, tzinfo=UTC)
    with connection:
        cursor = connection.execute(
            """
            INSERT INTO alarm_event(
                alarm_id, revision_id, origin, event_type, event_at_utc
            ) VALUES (?, NULL, 'ENGINEERING', ?, ?)
            """,
            (alarm_id, event_type, at.isoformat()),
        )
    return int(cursor.lastrowid)


def test_worker_dispatches_and_marks_sent(tmp_path: Path) -> None:
    connection = connect(tmp_path / "worker.db")
    apply_migrations(connection)
    enqueue_notification(
        connection,
        dedupe_key="TEMP_HI:ACTIVATE:mobile",
        alarm_id="TEMP_HI",
        event_type="ACTIVATE",
        route_key="mobile",
        payload={"message": "Temperature high"},
        now=datetime(2020, 1, 1, tzinfo=UTC),
    )
    delivered: list[str] = []

    async def dispatch(item) -> None:
        delivered.append(item.dedupe_key)

    worker = NotificationOutboxWorker(connection, dispatch)
    processed = asyncio.run(worker.process_once())

    assert processed == 1
    assert delivered == ["TEMP_HI:ACTIVATE:mobile"]
    row = connection.execute(
        "SELECT status, attempts FROM notification_outbox"
    ).fetchone()
    assert row == ("SENT", 1)
    connection.close()


def test_current_canonical_activation_dispatches(tmp_path: Path) -> None:
    connection = connect(tmp_path / "worker-current.db")
    apply_migrations(connection)
    source_event_id = _canonical_activation(connection)
    delivered: list[int] = []

    async def dispatch(item) -> None:
        delivered.append(item.outbox_id)

    worker = NotificationOutboxWorker(connection, dispatch)
    assert asyncio.run(worker.process_once()) == 1

    assert len(delivered) == 1
    row = connection.execute(
        "SELECT status, dedupe_key FROM notification_outbox"
    ).fetchone()
    assert row == ("SENT", f"alarm-event:{source_event_id}:policy:P1_PHONE")
    connection.close()


def test_claimed_activation_is_discarded_after_return_even_if_reactivated(tmp_path: Path) -> None:
    connection = connect(tmp_path / "worker-return-race.db")
    apply_migrations(connection)
    _canonical_activation(connection)
    _later_event(connection, "TEMP_HI", "RETURN", 1)
    _later_event(connection, "TEMP_HI", "REACTIVATE", 2)
    delivered: list[int] = []

    async def dispatch(item) -> None:
        delivered.append(item.outbox_id)

    worker = NotificationOutboxWorker(connection, dispatch)
    assert asyncio.run(worker.process_once()) == 1

    assert delivered == []
    assert connection.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0] == 0
    connection.close()


def test_claimed_activation_is_discarded_after_temporary_suppression(tmp_path: Path) -> None:
    connection = connect(tmp_path / "worker-suppress-race.db")
    apply_migrations(connection)
    _canonical_activation(connection)
    _later_event(connection, "TEMP_HI", "SUPPRESS", 1)
    _later_event(connection, "TEMP_HI", "UNSUPPRESS", 2)
    delivered: list[int] = []

    async def dispatch(item) -> None:
        delivered.append(item.outbox_id)

    worker = NotificationOutboxWorker(connection, dispatch)
    assert asyncio.run(worker.process_once()) == 1

    assert delivered == []
    assert connection.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0] == 0
    connection.close()


def test_worker_retries_dispatch_failure(tmp_path: Path) -> None:
    connection = connect(tmp_path / "worker-retry.db")
    apply_migrations(connection)
    enqueue_notification(
        connection,
        dedupe_key="TEMP_HI:ACTIVATE:mobile",
        alarm_id="TEMP_HI",
        event_type="ACTIVATE",
        route_key="mobile",
        payload={"message": "Temperature high"},
        now=datetime(2020, 1, 1, tzinfo=UTC),
    )

    async def fail(_item) -> None:
        raise RuntimeError("notify service unavailable")

    worker = NotificationOutboxWorker(
        connection,
        fail,
        max_attempts=2,
        retry_delay_s=0,
    )

    assert asyncio.run(worker.process_once()) == 1
    first = connection.execute(
        "SELECT status, attempts, last_error FROM notification_outbox"
    ).fetchone()
    assert first == ("PENDING", 1, "notify service unavailable")

    assert asyncio.run(worker.process_once()) == 1
    second = connection.execute(
        "SELECT status, attempts, last_error FROM notification_outbox"
    ).fetchone()
    assert second == ("FAILED", 2, "notify service unavailable")
    connection.close()


def test_permanent_failure_does_not_retry(tmp_path: Path) -> None:
    connection = connect(tmp_path / "worker-permanent.db")
    apply_migrations(connection)
    enqueue_notification(
        connection,
        dedupe_key="TEMP_HI:ACTIVATE:invalid",
        alarm_id="TEMP_HI",
        event_type="ACTIVATE",
        route_key="invalid",
        payload={"message": "Temperature high"},
        now=datetime(2020, 1, 1, tzinfo=UTC),
    )

    async def fail(_item) -> None:
        raise PermanentNotificationError("invalid route")

    worker = NotificationOutboxWorker(connection, fail, max_attempts=5, retry_delay_s=30)

    assert asyncio.run(worker.process_once()) == 1
    row = connection.execute(
        "SELECT status, attempts, last_error FROM notification_outbox"
    ).fetchone()
    assert row == ("FAILED", 1, "invalid route")
    assert asyncio.run(worker.process_once()) == 0
    connection.close()


def test_worker_start_and_stop_are_idempotent(tmp_path: Path) -> None:
    connection = connect(tmp_path / "worker-lifecycle.db")
    apply_migrations(connection)

    async def dispatch(_item) -> None:
        return None

    async def scenario() -> None:
        worker = NotificationOutboxWorker(connection, dispatch, poll_interval_s=60)
        await worker.start()
        first_task = worker._task
        await worker.start()
        assert worker.running is True
        assert worker._task is first_task
        await worker.stop()
        await worker.stop()
        assert worker.running is False

    asyncio.run(scenario())
    connection.close()
