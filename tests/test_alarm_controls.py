from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    TagDefinition,
)
from open_alarm.backend.db.alarm_control_repository import (
    AlarmControlError,
    expire_shelves,
    set_out_of_service,
    set_suppressed,
    shelve_alarm,
    unshelve_alarm,
)
from open_alarm.backend.db.alarm_query_repository import list_alarm_history, list_alarm_states
from open_alarm.backend.db.config_repository import store_compiled_revision
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.ha.models import normalize_entity_state
from open_alarm.backend.runtime.dispatcher import AlarmDispatcher
from open_alarm.backend.runtime.system_alarms import SystemAlarmManager

ENGINEERING_ALARM_ID = "TEMP_HI"
SYSTEM_ALARM_ID = "SYS_HA_CONNECTION_LOST"


def _state(value: str, at: datetime):
    return normalize_entity_state(
        "sensor.temp",
        {"state": value, "attributes": {}},
        observed_at=at,
        source_timestamp=at,
    )


def _active_engineering_alarm(tmp_path: Path):
    connection = connect(tmp_path / "controls.db")
    apply_migrations(connection)
    compiled = CompiledConfig(
        schema_version="1.0.0",
        source_hash="control-test",
        tags=(TagDefinition("TEMP", "sensor.temp"),),
        alarms=(
            AlarmDefinition(
                alarm_id=ENGINEERING_ALARM_ID,
                source_tag_id="TEMP",
                kind=AlarmKind.ANALOG,
                condition="HIGH",
                priority="P1",
                category="PROCESS",
                setpoint=80.0,
                hysteresis=2.0,
            ),
        ),
    )
    store_compiled_revision(connection, compiled, revision_id="control-r1")
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    dispatcher = AlarmDispatcher(compiled, revision_id="control-r1", connection=connection)
    dispatcher.process_entity(_state("90", now), now=now)
    return connection, now


def _active_system_alarm(tmp_path: Path):
    connection = connect(tmp_path / "system-controls.db")
    apply_migrations(connection)
    manager = SystemAlarmManager(connection)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    manager.set_ha_connected(False, reason="test", now=start)
    manager.tick(now=start + timedelta(seconds=10))
    return connection, start + timedelta(seconds=10)


def test_shelving_is_orthogonal_and_expires_with_audit(tmp_path: Path) -> None:
    connection, now = _active_engineering_alarm(tmp_path)
    before = connection.execute(
        "SELECT lifecycle, pending_deadline_utc FROM alarm_state WHERE alarm_id = ?",
        (ENGINEERING_ALARM_ID,),
    ).fetchone()

    until = shelve_alarm(
        connection,
        ENGINEERING_ALARM_ID,
        duration_s=60,
        user_id="operator",
        reason="maintenance check",
        now=now,
    )

    after = connection.execute(
        "SELECT lifecycle, pending_deadline_utc, shelved_until_utc FROM alarm_state WHERE alarm_id = ?",
        (ENGINEERING_ALARM_ID,),
    ).fetchone()
    assert after[:2] == before
    assert after[2] == until.isoformat()
    assert list_alarm_states(connection, view="active") == []
    assert [item["alarm_id"] for item in list_alarm_states(connection, view="shelved")] == [
        ENGINEERING_ALARM_ID
    ]

    assert expire_shelves(connection, now=until + timedelta(seconds=1)) == (
        ENGINEERING_ALARM_ID,
    )
    assert [item["alarm_id"] for item in list_alarm_states(connection, view="active")] == [
        ENGINEERING_ALARM_ID
    ]
    events = list_alarm_history(connection, alarm_id=ENGINEERING_ALARM_ID, limit=10)
    assert events[0]["event_type"] == "UNSHELVE"
    assert events[0]["details"] == {"reason": "EXPIRED"}
    assert events[1]["event_type"] == "SHELVE"
    assert events[1]["details"]["reason"] == "maintenance check"
    connection.close()


def test_suppression_and_oos_do_not_change_lifecycle(tmp_path: Path) -> None:
    connection, now = _active_engineering_alarm(tmp_path)
    lifecycle = connection.execute(
        "SELECT lifecycle FROM alarm_state WHERE alarm_id = ?",
        (ENGINEERING_ALARM_ID,),
    ).fetchone()[0]

    assert set_suppressed(
        connection,
        ENGINEERING_ALARM_ID,
        suppressed=True,
        user_id="engineer",
        reason="parent equipment suppressed",
        now=now,
    ) is True
    assert list_alarm_states(connection, view="active") == []
    assert [item["alarm_id"] for item in list_alarm_states(connection, view="suppressed")] == [
        ENGINEERING_ALARM_ID
    ]
    assert connection.execute(
        "SELECT lifecycle FROM alarm_state WHERE alarm_id = ?",
        (ENGINEERING_ALARM_ID,),
    ).fetchone()[0] == lifecycle

    assert set_suppressed(
        connection,
        ENGINEERING_ALARM_ID,
        suppressed=False,
        user_id="engineer",
        reason="suppression cleared",
        now=now,
    ) is True
    assert set_out_of_service(
        connection,
        ENGINEERING_ALARM_ID,
        out_of_service=True,
        user_id="engineer",
        reason="alarm under engineering",
        now=now,
    ) is True
    assert list_alarm_states(connection, view="active") == []
    assert [item["alarm_id"] for item in list_alarm_states(connection, view="out_of_service")] == [
        ENGINEERING_ALARM_ID
    ]
    assert connection.execute(
        "SELECT lifecycle FROM alarm_state WHERE alarm_id = ?",
        (ENGINEERING_ALARM_ID,),
    ).fetchone()[0] == lifecycle

    events = [
        item["event_type"]
        for item in list_alarm_history(connection, alarm_id=ENGINEERING_ALARM_ID)
    ]
    assert events[:3] == ["OUT_OF_SERVICE", "UNSUPPRESS", "SUPPRESS"]
    connection.close()


def test_system_alarm_cannot_be_hidden_but_recovery_controls_are_allowed(tmp_path: Path) -> None:
    connection, now = _active_system_alarm(tmp_path)

    with pytest.raises(AlarmControlError, match="cannot be shelved"):
        shelve_alarm(
            connection,
            SYSTEM_ALARM_ID,
            duration_s=60,
            user_id="operator",
            reason="not allowed",
            now=now,
        )
    with pytest.raises(AlarmControlError, match="cannot be suppressed"):
        set_suppressed(
            connection,
            SYSTEM_ALARM_ID,
            suppressed=True,
            user_id="engineer",
            reason="not allowed",
            now=now,
        )
    with pytest.raises(AlarmControlError, match="cannot be taken out of service"):
        set_out_of_service(
            connection,
            SYSTEM_ALARM_ID,
            out_of_service=True,
            user_id="engineer",
            reason="not allowed",
            now=now,
        )

    state = connection.execute(
        """
        SELECT shelved_until_utc, suppressed, out_of_service
        FROM alarm_state WHERE alarm_id = ?
        """,
        (SYSTEM_ALARM_ID,),
    ).fetchone()
    assert state == (None, 0, 0)
    assert [item["alarm_id"] for item in list_alarm_states(connection, view="active")] == [
        SYSTEM_ALARM_ID
    ]

    # Recovery remains possible if corrupt/legacy data contains hidden flags.
    with connection:
        connection.execute(
            """
            UPDATE alarm_state
            SET shelved_until_utc = ?, suppressed = 1, out_of_service = 1
            WHERE alarm_id = ?
            """,
            ((now + timedelta(hours=1)).isoformat(), SYSTEM_ALARM_ID),
        )
    assert unshelve_alarm(
        connection,
        SYSTEM_ALARM_ID,
        user_id="operator",
        reason="recovery",
        now=now,
    ) is True
    assert set_suppressed(
        connection,
        SYSTEM_ALARM_ID,
        suppressed=False,
        user_id="engineer",
        reason="recovery",
        now=now,
    ) is True
    assert set_out_of_service(
        connection,
        SYSTEM_ALARM_ID,
        out_of_service=False,
        user_id="engineer",
        reason="recovery",
        now=now,
    ) is True
    connection.close()
