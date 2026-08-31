from datetime import UTC, datetime, timedelta
from pathlib import Path

from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.domain.models import AlarmLifecycle
from open_alarm.backend.runtime.system_alarms import (
    HA_CONNECTION_ALARM_ID,
    HA_CONNECTION_ON_DELAY_S,
    NOTIFICATION_DELIVERY_ALARM_ID,
    NOTIFICATION_WORKER_ALARM_ID,
    SystemAlarmManager,
)


def test_connection_loss_is_first_class_system_alarm(tmp_path: Path) -> None:
    connection = connect(tmp_path / "system.db")
    apply_migrations(connection)
    manager = SystemAlarmManager(connection)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)

    manager.set_ha_connected(False, reason="socket closed", now=start)
    assert manager.alarm_state(HA_CONNECTION_ALARM_ID).lifecycle == AlarmLifecycle.PENDING_ON

    manager.tick(now=start + timedelta(seconds=HA_CONNECTION_ON_DELAY_S))
    assert manager.alarm_state(HA_CONNECTION_ALARM_ID).lifecycle == AlarmLifecycle.ACTIVE_UNACK

    row = connection.execute(
        "SELECT revision_id, origin, lifecycle FROM alarm_state WHERE alarm_id = ?",
        (HA_CONNECTION_ALARM_ID,),
    ).fetchone()
    assert row == (None, "SYSTEM", AlarmLifecycle.ACTIVE_UNACK.value)

    events = connection.execute(
        "SELECT origin, event_type FROM alarm_event WHERE alarm_id = ? ORDER BY event_id",
        (HA_CONNECTION_ALARM_ID,),
    ).fetchall()
    assert events == [("SYSTEM", "PENDING_ON"), ("SYSTEM", "ACTIVATE")]
    connection.close()


def test_connection_alarm_survives_restart_until_ha_reconnects(tmp_path: Path) -> None:
    db_path = tmp_path / "restart.db"
    connection = connect(db_path)
    apply_migrations(connection)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    manager = SystemAlarmManager(connection)
    manager.set_ha_connected(False, reason="network", now=start)
    manager.tick(now=start + timedelta(seconds=10))
    connection.close()

    reopened = connect(db_path)
    apply_migrations(reopened)
    restored = SystemAlarmManager(reopened)
    assert restored.alarm_state(HA_CONNECTION_ALARM_ID).lifecycle == AlarmLifecycle.ACTIVE_UNACK

    restored.set_ha_connected(True, now=start + timedelta(seconds=20))
    assert restored.alarm_state(HA_CONNECTION_ALARM_ID).lifecycle == AlarmLifecycle.NORMAL

    events = reopened.execute(
        "SELECT event_type FROM alarm_event WHERE alarm_id = ? ORDER BY event_id",
        (HA_CONNECTION_ALARM_ID,),
    ).fetchall()
    assert events[-1] == ("RETURN",)
    reopened.close()


def test_notification_delivery_fault_uses_normal_alarm_lifecycle(tmp_path: Path) -> None:
    connection = connect(tmp_path / "notifications.db")
    apply_migrations(connection)
    manager = SystemAlarmManager(connection)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)

    manager.set_condition(
        NOTIFICATION_DELIVERY_ALARM_ID,
        True,
        raw_value={"failed": 2, "pending_due": 0},
        now=start,
    )
    assert manager.alarm_state(NOTIFICATION_DELIVERY_ALARM_ID).lifecycle == (
        AlarmLifecycle.ACTIVE_UNACK
    )

    manager.acknowledge(
        NOTIFICATION_DELIVERY_ALARM_ID,
        user_id="operator",
        now=start + timedelta(seconds=1),
    )
    assert manager.alarm_state(NOTIFICATION_DELIVERY_ALARM_ID).lifecycle == AlarmLifecycle.ACTIVE_ACK

    manager.set_condition(
        NOTIFICATION_DELIVERY_ALARM_ID,
        False,
        raw_value={"failed": 0, "pending_due": 0},
        now=start + timedelta(seconds=2),
    )
    assert manager.alarm_state(NOTIFICATION_DELIVERY_ALARM_ID).lifecycle == AlarmLifecycle.NORMAL

    events = connection.execute(
        "SELECT event_type FROM alarm_event WHERE alarm_id = ? ORDER BY event_id",
        (NOTIFICATION_DELIVERY_ALARM_ID,),
    ).fetchall()
    assert events == [("ACTIVATE",), ("ACK",), ("RETURN",)]
    connection.close()


def test_notification_worker_fault_survives_manager_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    connection = connect(db_path)
    apply_migrations(connection)
    manager = SystemAlarmManager(connection)
    at = datetime(2026, 8, 30, 12, tzinfo=UTC)
    manager.set_condition(
        NOTIFICATION_WORKER_ALARM_ID,
        True,
        raw_value={"running": False},
        now=at,
    )
    connection.close()

    reopened = connect(db_path)
    apply_migrations(reopened)
    restored = SystemAlarmManager(reopened)
    assert restored.alarm_state(NOTIFICATION_WORKER_ALARM_ID).lifecycle == (
        AlarmLifecycle.ACTIVE_UNACK
    )
    restored.set_condition(
        NOTIFICATION_WORKER_ALARM_ID,
        False,
        raw_value={"running": True},
        now=at + timedelta(seconds=1),
    )
    assert restored.alarm_state(NOTIFICATION_WORKER_ALARM_ID).lifecycle == AlarmLifecycle.NORMAL
    reopened.close()


def test_stable_system_condition_does_not_write_again(tmp_path: Path) -> None:
    connection = connect(tmp_path / "stable.db")
    apply_migrations(connection)
    manager = SystemAlarmManager(connection)
    at = datetime(2026, 8, 30, 12, tzinfo=UTC)

    manager.set_condition(
        NOTIFICATION_WORKER_ALARM_ID,
        True,
        raw_value={"running": False},
        now=at,
    )
    before = connection.execute(
        "SELECT updated_at_utc FROM alarm_state WHERE alarm_id = ?",
        (NOTIFICATION_WORKER_ALARM_ID,),
    ).fetchone()[0]
    manager.set_condition(
        NOTIFICATION_WORKER_ALARM_ID,
        True,
        raw_value={"running": False},
        now=at + timedelta(hours=1),
    )
    after = connection.execute(
        "SELECT updated_at_utc FROM alarm_state WHERE alarm_id = ?",
        (NOTIFICATION_WORKER_ALARM_ID,),
    ).fetchone()[0]
    assert after == before
    connection.close()


def test_runtime_events_are_audited(tmp_path: Path) -> None:
    connection = connect(tmp_path / "audit.db")
    apply_migrations(connection)
    manager = SystemAlarmManager(connection)
    at = datetime(2026, 8, 30, 12, tzinfo=UTC)

    manager.record_runtime_event("START", at=at)
    manager.set_ha_connected(False, reason="test", now=at)

    events = connection.execute(
        "SELECT event_type FROM runtime_event ORDER BY event_id"
    ).fetchall()
    assert events == [("START",), ("HA_DISCONNECTED",)]
    connection.close()
