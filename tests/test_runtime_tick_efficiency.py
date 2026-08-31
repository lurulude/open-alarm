from datetime import UTC, datetime, timedelta
from pathlib import Path

from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    TagDefinition,
)
from open_alarm.backend.db.config_repository import store_compiled_revision
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.domain.models import AlarmEventType, AlarmLifecycle
from open_alarm.backend.ha.models import normalize_entity_state
from open_alarm.backend.runtime.dispatcher import AlarmDispatcher


def _state(value: str, at: datetime):
    return normalize_entity_state(
        "sensor.temp",
        {"state": value, "attributes": {}},
        observed_at=at,
        source_timestamp=at,
    )


def _compiled(alarm: AlarmDefinition) -> CompiledConfig:
    return CompiledConfig(
        schema_version="1.0.0",
        source_hash=f"tick-efficiency-{alarm.alarm_id}",
        tags=(TagDefinition("TEMP", "sensor.temp"),),
        alarms=(alarm,),
    )


def test_normal_source_updates_and_quiet_tick_do_not_write_alarm_state(tmp_path: Path) -> None:
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    alarm = AlarmDefinition(
        alarm_id="TEMP_HI",
        source_tag_id="TEMP",
        kind=AlarmKind.ANALOG,
        condition="HIGH",
        priority="P2",
        category="PROCESS",
        setpoint=80.0,
        hysteresis=2.0,
    )
    compiled = _compiled(alarm)
    connection = connect(tmp_path / "quiet.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")
    dispatcher = AlarmDispatcher(compiled, revision_id="r1", connection=connection)
    before_changes = connection.total_changes

    dispatcher.process_entity(_state("20", start), now=start)
    dispatcher.process_entity(_state("21", start + timedelta(milliseconds=500)))
    assert dispatcher.tick(now=start + timedelta(seconds=1)) == ()

    assert connection.total_changes == before_changes
    assert connection.execute(
        "SELECT alarm_id FROM alarm_state WHERE alarm_id = ?",
        (alarm.alarm_id,),
    ).fetchone() is None
    connection.close()


def test_pending_timer_writes_only_when_deadline_is_due(tmp_path: Path) -> None:
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    alarm = AlarmDefinition(
        alarm_id="TEMP_HI_DELAYED",
        source_tag_id="TEMP",
        kind=AlarmKind.ANALOG,
        condition="HIGH",
        priority="P1",
        category="PROCESS",
        setpoint=80.0,
        hysteresis=2.0,
        on_delay_s=10.0,
    )
    compiled = _compiled(alarm)
    connection = connect(tmp_path / "pending.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")
    dispatcher = AlarmDispatcher(compiled, revision_id="r1", connection=connection)

    dispatcher.process_entity(_state("90", start), now=start)
    assert dispatcher.alarm_state(alarm.alarm_id).lifecycle == AlarmLifecycle.PENDING_ON
    before_changes = connection.total_changes

    assert dispatcher.tick(now=start + timedelta(seconds=5)) == ()
    assert connection.total_changes == before_changes

    due = dispatcher.tick(now=start + timedelta(seconds=10))
    assert len(due) == 1
    assert dispatcher.alarm_state(alarm.alarm_id).lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert connection.total_changes > before_changes
    events = connection.execute(
        "SELECT event_type FROM alarm_event WHERE alarm_id = ? ORDER BY event_id",
        (alarm.alarm_id,),
    ).fetchall()
    assert events == [("PENDING_ON",), ("ACTIVATE",)]
    connection.close()


def test_digital_debounce_is_persisted_and_bad_quality_clears_it(tmp_path: Path) -> None:
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    alarm = AlarmDefinition(
        alarm_id="CONTACT_FAULT",
        source_tag_id="TEMP",
        kind=AlarmKind.DIGITAL,
        condition="EQUALS",
        priority="P2",
        category="PROCESS",
        alarm_value="on",
        debounce_on_s=10.0,
    )
    compiled = _compiled(alarm)
    connection = connect(tmp_path / "debounce.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")
    dispatcher = AlarmDispatcher(compiled, revision_id="r1", connection=connection)

    dispatcher.process_entity(_state("on", start), now=start)
    row = connection.execute(
        """
        SELECT lifecycle, debounce_pending_target, debounce_pending_deadline_utc
        FROM alarm_state WHERE alarm_id = ?
        """,
        (alarm.alarm_id,),
    ).fetchone()
    assert row == ("NORMAL", 1, (start + timedelta(seconds=10)).isoformat())

    dispatcher.process_entity(
        _state("unavailable", start + timedelta(seconds=1)),
        now=start + timedelta(seconds=1),
    )
    cleared = connection.execute(
        """
        SELECT lifecycle, debounce_pending_target, debounce_pending_deadline_utc
        FROM alarm_state WHERE alarm_id = ?
        """,
        (alarm.alarm_id,),
    ).fetchone()
    assert cleared == ("NORMAL", None, None)
    connection.close()


def test_latched_analog_reactivation_requires_activation_threshold() -> None:
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    alarm = AlarmDefinition(
        alarm_id="TEMP_HI_LATCH",
        source_tag_id="TEMP",
        kind=AlarmKind.ANALOG,
        condition="HIGH",
        priority="P1",
        category="PROCESS",
        setpoint=80.0,
        hysteresis=2.0,
        latching=True,
    )
    dispatcher = AlarmDispatcher(_compiled(alarm), revision_id="r1")

    dispatcher.process_entity(_state("90", start), now=start)
    dispatcher.process_entity(_state("70", start + timedelta(seconds=1)))
    state = dispatcher.alarm_state(alarm.alarm_id)
    assert state.condition_abnormal is False
    assert state.returned_at == start + timedelta(seconds=1)

    deadband = dispatcher.process_entity(_state("79", start + timedelta(seconds=2)))
    assert dispatcher.alarm_state(alarm.alarm_id).condition_abnormal is False
    assert all(
        event.event_type != AlarmEventType.REACTIVATE
        for result in deadband
        for event in result.events
    )

    reactivated = dispatcher.process_entity(_state("80", start + timedelta(seconds=3)))
    assert dispatcher.alarm_state(alarm.alarm_id).condition_abnormal is True
    assert any(
        event.event_type == AlarmEventType.REACTIVATE
        for result in reactivated
        for event in result.events
    )
