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
from open_alarm.backend.domain.models import AlarmLifecycle
from open_alarm.backend.ha.models import normalize_entity_state
from open_alarm.backend.runtime.dispatcher import AlarmDispatcher


def _state(entity_id: str, value: str, at: datetime):
    return normalize_entity_state(
        entity_id,
        {"state": value, "attributes": {}},
        observed_at=at,
        source_timestamp=at,
    )


def _compiled(*alarms: AlarmDefinition, stale_after_s: float | None = None) -> CompiledConfig:
    return CompiledConfig(
        schema_version="1.0.0",
        source_hash="runtime-test-hash",
        tags=(TagDefinition("TEMP", "sensor.temp", stale_after_s=stale_after_s),),
        alarms=alarms,
    )


def test_analog_alarm_does_not_clear_on_unavailable() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    alarm = AlarmDefinition(
        alarm_id="TEMP_HI",
        source_tag_id="TEMP",
        kind=AlarmKind.ANALOG,
        condition="HIGH",
        setpoint=80.0,
        hysteresis=2.0,
        priority="P1",
        category="PROCESS",
    )
    dispatcher = AlarmDispatcher(_compiled(alarm), revision_id="r1")

    dispatcher.process_entity(_state("sensor.temp", "90", start), now=start)
    assert dispatcher.alarm_state("TEMP_HI").lifecycle == AlarmLifecycle.ACTIVE_UNACK

    unavailable = _state("sensor.temp", "unavailable", start + timedelta(seconds=1))
    dispatcher.process_entity(unavailable, now=start + timedelta(seconds=1))
    assert dispatcher.alarm_state("TEMP_HI").lifecycle == AlarmLifecycle.ACTIVE_UNACK


def test_device_alarm_tracks_unavailable_quality() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    alarm = AlarmDefinition(
        alarm_id="TEMP_UNAVAILABLE",
        source_tag_id="TEMP",
        kind=AlarmKind.DEVICE,
        condition="UNAVAILABLE",
        priority="P2",
        category="DEVICE",
    )
    dispatcher = AlarmDispatcher(_compiled(alarm), revision_id="r1")

    dispatcher.process_entity(_state("sensor.temp", "unavailable", start), now=start)
    assert dispatcher.alarm_state("TEMP_UNAVAILABLE").lifecycle == AlarmLifecycle.ACTIVE_UNACK


def test_digital_debounce_precedes_alarm_on_delay() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    alarm = AlarmDefinition(
        alarm_id="CONTACT_FAULT",
        source_tag_id="TEMP",
        kind=AlarmKind.DIGITAL,
        condition="EQUALS",
        alarm_value="on",
        debounce_on_s=5,
        on_delay_s=10,
        priority="P2",
        category="PROCESS",
    )
    dispatcher = AlarmDispatcher(_compiled(alarm), revision_id="r1")

    dispatcher.process_entity(_state("sensor.temp", "on", start), now=start)
    assert dispatcher.alarm_state("CONTACT_FAULT").lifecycle == AlarmLifecycle.NORMAL

    dispatcher.tick(now=start + timedelta(seconds=5))
    state = dispatcher.alarm_state("CONTACT_FAULT")
    assert state.lifecycle == AlarmLifecycle.PENDING_ON
    assert state.pending_deadline == start + timedelta(seconds=15)

    dispatcher.tick(now=start + timedelta(seconds=15))
    assert dispatcher.alarm_state("CONTACT_FAULT").lifecycle == AlarmLifecycle.ACTIVE_UNACK


def test_bad_quality_cancels_pending_return_and_keeps_active_alarm() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    alarm = AlarmDefinition(
        alarm_id="CONTACT_FAULT",
        source_tag_id="TEMP",
        kind=AlarmKind.DIGITAL,
        condition="EQUALS",
        alarm_value="on",
        off_delay_s=10,
        priority="P2",
        category="PROCESS",
    )
    dispatcher = AlarmDispatcher(_compiled(alarm), revision_id="r1")

    dispatcher.process_entity(_state("sensor.temp", "on", start), now=start)
    dispatcher.process_entity(_state("sensor.temp", "off", start + timedelta(seconds=1)))
    assert dispatcher.alarm_state("CONTACT_FAULT").lifecycle == AlarmLifecycle.PENDING_OFF

    dispatcher.process_entity(
        _state("sensor.temp", "unavailable", start + timedelta(seconds=2)),
        now=start + timedelta(seconds=2),
    )
    assert dispatcher.alarm_state("CONTACT_FAULT").lifecycle == AlarmLifecycle.ACTIVE_UNACK


def test_stale_quality_alarm_is_raised_without_new_entity_event() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    alarm = AlarmDefinition(
        alarm_id="TEMP_STALE",
        source_tag_id="TEMP",
        kind=AlarmKind.DEVICE,
        condition="STALE",
        priority="P2",
        category="DEVICE",
    )
    dispatcher = AlarmDispatcher(_compiled(alarm, stale_after_s=30), revision_id="r1")

    dispatcher.process_entity(_state("sensor.temp", "20", start), now=start)
    assert dispatcher.alarm_state("TEMP_STALE").lifecycle == AlarmLifecycle.NORMAL

    dispatcher.tick(now=start + timedelta(seconds=30))
    assert dispatcher.alarm_state("TEMP_STALE").lifecycle == AlarmLifecycle.ACTIVE_UNACK


def test_pending_on_deadline_survives_dispatcher_restart(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    alarm = AlarmDefinition(
        alarm_id="TEMP_HI",
        source_tag_id="TEMP",
        kind=AlarmKind.ANALOG,
        condition="HIGH",
        setpoint=80.0,
        hysteresis=2.0,
        on_delay_s=10,
        priority="P1",
        category="PROCESS",
    )
    compiled = _compiled(alarm)
    connection = connect(tmp_path / "runtime.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")

    first = AlarmDispatcher(compiled, revision_id="r1", connection=connection)
    first.process_entity(_state("sensor.temp", "90", start), now=start)
    assert first.alarm_state("TEMP_HI").lifecycle == AlarmLifecycle.PENDING_ON

    second = AlarmDispatcher(compiled, revision_id="r1", connection=connection)
    second.process_entity(
        _state("sensor.temp", "90", start + timedelta(seconds=11)),
        now=start + timedelta(seconds=11),
    )
    assert second.alarm_state("TEMP_HI").lifecycle == AlarmLifecycle.ACTIVE_UNACK

    events = connection.execute(
        "SELECT event_type FROM alarm_event WHERE alarm_id = ? ORDER BY event_id",
        ("TEMP_HI",),
    ).fetchall()
    assert [row[0] for row in events] == ["PENDING_ON", "ACTIVATE"]
