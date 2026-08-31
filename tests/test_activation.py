from datetime import UTC, datetime
from pathlib import Path

import pytest

from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    TagDefinition,
)
from open_alarm.backend.db.activation import RevisionActivationError, activate_revision
from open_alarm.backend.db.config_repository import (
    load_active_compiled_config,
    store_compiled_revision,
)
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.domain.models import AlarmLifecycle
from open_alarm.backend.engineering.service import preview_revision
from open_alarm.backend.ha.models import normalize_entity_state
from open_alarm.backend.runtime.dispatcher import AlarmDispatcher


def _alarm(
    *,
    source_tag_id: str = "TEMP",
    setpoint: float = 80.0,
    on_delay_s: float = 0.0,
    priority: str = "P1",
    message: str = "",
    latching: bool = False,
) -> AlarmDefinition:
    return AlarmDefinition(
        alarm_id="TEMP_HI",
        source_tag_id=source_tag_id,
        kind=AlarmKind.ANALOG,
        condition="HIGH",
        priority=priority,
        category="PROCESS",
        message=message,
        setpoint=setpoint,
        hysteresis=2.0,
        on_delay_s=on_delay_s,
        latching=latching,
    )


def _compiled(
    source_hash: str,
    *,
    alarm: AlarmDefinition | None,
    tag_id: str = "TEMP",
    entity_id: str = "sensor.temp",
) -> CompiledConfig:
    return CompiledConfig(
        schema_version="1.0.0",
        source_hash=source_hash,
        tags=(TagDefinition(tag_id, entity_id),),
        alarms=() if alarm is None else (alarm,),
    )


def _state(entity_id: str, value: str, at: datetime):
    return normalize_entity_state(
        entity_id,
        {"state": value, "attributes": {}},
        observed_at=at,
        source_timestamp=at,
    )


def _activate_alarm(connection, compiled: CompiledConfig, revision_id: str) -> None:
    at = datetime(2026, 8, 31, 7, tzinfo=UTC)
    dispatcher = AlarmDispatcher(compiled, revision_id=revision_id, connection=connection)
    dispatcher.process_entity(_state("sensor.temp", "90", at), now=at)
    assert dispatcher.alarm_state("TEMP_HI").lifecycle == AlarmLifecycle.ACTIVE_UNACK


def test_activation_preserves_live_alarm_for_setpoint_change(tmp_path: Path) -> None:
    connection = connect(tmp_path / "setpoint.db")
    apply_migrations(connection)
    rev1 = _compiled("setpoint-1", alarm=_alarm(setpoint=80))
    rev2 = _compiled("setpoint-2", alarm=_alarm(setpoint=95))
    store_compiled_revision(connection, rev1, revision_id="rev-1")
    store_compiled_revision(connection, rev2, revision_id="rev-2")
    activate_revision(connection, "rev-1")
    _activate_alarm(connection, rev1, "rev-1")

    preview = preview_revision(connection, "rev-2")
    assert "blocking_alarm_ids" not in preview
    result = activate_revision(connection, "rev-2")

    state = connection.execute(
        "SELECT revision_id, lifecycle FROM alarm_state WHERE alarm_id = ?",
        ("TEMP_HI",),
    ).fetchone()
    assert state == ("rev-2", AlarmLifecycle.ACTIVE_UNACK.value)
    assert result.migrated_alarm_ids == ("TEMP_HI",)
    assert result.reset_alarm_ids == ()
    connection.close()


def test_activation_resets_live_alarm_for_delay_change(tmp_path: Path) -> None:
    connection = connect(tmp_path / "delay.db")
    apply_migrations(connection)
    rev1 = _compiled("delay-1", alarm=_alarm())
    rev2 = _compiled("delay-2", alarm=_alarm(on_delay_s=30))
    store_compiled_revision(connection, rev1, revision_id="rev-1")
    store_compiled_revision(connection, rev2, revision_id="rev-2")
    activate_revision(connection, "rev-1")
    _activate_alarm(connection, rev1, "rev-1")

    result = activate_revision(connection, "rev-2")

    assert connection.execute(
        "SELECT alarm_id FROM alarm_state WHERE alarm_id = ?",
        ("TEMP_HI",),
    ).fetchone() is None
    assert result.reset_alarm_ids == ("TEMP_HI",)
    assert load_active_compiled_config(connection)[0] == "rev-2"
    connection.close()


def test_activation_resets_live_alarm_when_removed(tmp_path: Path) -> None:
    connection = connect(tmp_path / "removed.db")
    apply_migrations(connection)
    rev1 = _compiled("removed-1", alarm=_alarm())
    rev2 = _compiled("removed-2", alarm=None)
    store_compiled_revision(connection, rev1, revision_id="rev-1")
    store_compiled_revision(connection, rev2, revision_id="rev-2")
    activate_revision(connection, "rev-1")
    _activate_alarm(connection, rev1, "rev-1")

    result = activate_revision(connection, "rev-2")

    assert connection.execute(
        "SELECT alarm_id FROM alarm_state WHERE alarm_id = ?",
        ("TEMP_HI",),
    ).fetchone() is None
    assert result.reset_alarm_ids == ("TEMP_HI",)
    assert load_active_compiled_config(connection)[0] == "rev-2"
    connection.close()


def test_activation_resets_live_alarm_when_source_changes(tmp_path: Path) -> None:
    connection = connect(tmp_path / "source.db")
    apply_migrations(connection)
    rev1 = _compiled("source-1", alarm=_alarm())
    rev2 = _compiled(
        "source-2",
        alarm=_alarm(source_tag_id="TEMP_2"),
        tag_id="TEMP_2",
        entity_id="sensor.temp_2",
    )
    store_compiled_revision(connection, rev1, revision_id="rev-1")
    store_compiled_revision(connection, rev2, revision_id="rev-2")
    activate_revision(connection, "rev-1")
    _activate_alarm(connection, rev1, "rev-1")

    result = activate_revision(connection, "rev-2")

    assert connection.execute(
        "SELECT alarm_id FROM alarm_state WHERE alarm_id = ?",
        ("TEMP_HI",),
    ).fetchone() is None
    assert result.reset_alarm_ids == ("TEMP_HI",)
    connection.close()


def test_activation_preserves_live_alarm_for_metadata_change(tmp_path: Path) -> None:
    connection = connect(tmp_path / "metadata.db")
    apply_migrations(connection)
    rev1 = _compiled("metadata-1", alarm=_alarm(priority="P1", message="Old"))
    rev2 = _compiled("metadata-2", alarm=_alarm(priority="P2", message="New"))
    store_compiled_revision(connection, rev1, revision_id="rev-1")
    store_compiled_revision(connection, rev2, revision_id="rev-2")
    activate_revision(connection, "rev-1")
    _activate_alarm(connection, rev1, "rev-1")

    result = activate_revision(connection, "rev-2")

    state = connection.execute(
        "SELECT revision_id, lifecycle FROM alarm_state WHERE alarm_id = ?",
        ("TEMP_HI",),
    ).fetchone()
    assert state == ("rev-2", AlarmLifecycle.ACTIVE_UNACK.value)
    assert result.migrated_alarm_ids == ("TEMP_HI",)
    connection.close()


def test_active_system_alarm_is_untouched(tmp_path: Path) -> None:
    connection = connect(tmp_path / "system.db")
    apply_migrations(connection)
    rev1 = _compiled("system-1", alarm=_alarm())
    rev2 = _compiled("system-2", alarm=_alarm(setpoint=85))
    store_compiled_revision(connection, rev1, revision_id="rev-1")
    store_compiled_revision(connection, rev2, revision_id="rev-2")
    activate_revision(connection, "rev-1")

    at = datetime(2026, 8, 31, 7, tzinfo=UTC).isoformat()
    with connection:
        connection.execute(
            """
            INSERT INTO alarm_state(
                alarm_id, revision_id, origin, lifecycle,
                condition_abnormal, updated_at_utc
            ) VALUES ('SYS_HA_CONNECTION_LOST', NULL, 'SYSTEM', 'ACTIVE_UNACK', 1, ?)
            """,
            (at,),
        )

    activate_revision(connection, "rev-2")

    system_state = connection.execute(
        """
        SELECT origin, lifecycle, revision_id
        FROM alarm_state WHERE alarm_id = 'SYS_HA_CONNECTION_LOST'
        """
    ).fetchone()
    assert system_state == ("SYSTEM", "ACTIVE_UNACK", None)
    connection.close()


def test_activation_rejects_unknown_revision(tmp_path: Path) -> None:
    connection = connect(tmp_path / "missing.db")
    apply_migrations(connection)

    with pytest.raises(RevisionActivationError):
        activate_revision(connection, "missing")

    connection.close()
