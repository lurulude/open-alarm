from datetime import UTC, datetime, timedelta
from pathlib import Path

from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    TagDefinition,
)
from open_alarm.backend.db.alarm_query_repository import list_alarm_history, list_alarm_states
from open_alarm.backend.db.config_repository import store_compiled_revision
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.domain.models import AlarmLifecycle
from open_alarm.backend.ha.models import normalize_entity_state
from open_alarm.backend.runtime.dispatcher import AlarmDispatcher

BLOCKER = "COMMS.FAIL"
CHILD = "PUMP.TRIP"


def _state(entity_id: str, value: str, at: datetime):
    return normalize_entity_state(
        entity_id,
        {"state": value, "attributes": {}},
        observed_at=at,
        source_timestamp=at,
    )


def _compiled(*, blocker_on_delay_s: float = 0.0, blocker_latching: bool = False) -> CompiledConfig:
    return CompiledConfig(
        schema_version="1.0.0",
        source_hash=f"inhibition-{blocker_on_delay_s}-{blocker_latching}",
        tags=(
            TagDefinition("COMMS", "binary_sensor.comms_fail"),
            TagDefinition("PUMP", "binary_sensor.pump_trip"),
        ),
        alarms=(
            AlarmDefinition(
                alarm_id=BLOCKER,
                source_tag_id="COMMS",
                kind=AlarmKind.DIGITAL,
                condition="EQUALS",
                priority="P1",
                category="COMMS",
                alarm_value="on",
                on_delay_s=blocker_on_delay_s,
                latching=blocker_latching,
            ),
            AlarmDefinition(
                alarm_id=CHILD,
                source_tag_id="PUMP",
                kind=AlarmKind.DIGITAL,
                condition="EQUALS",
                priority="P2",
                category="PROCESS",
                alarm_value="on",
                inhibit_by_alarm_ids=(BLOCKER,),
            ),
        ),
    )


def _dispatcher(tmp_path: Path, compiled: CompiledConfig):
    connection = connect(tmp_path / "inhibition.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="rev-inhibit")
    with connection:
        connection.execute(
            "UPDATE config_revision SET active = 1 WHERE revision_id = 'rev-inhibit'"
        )
    return connection, AlarmDispatcher(compiled, revision_id="rev-inhibit", connection=connection)


def test_active_inhibitor_hides_child_without_stopping_lifecycle(tmp_path: Path) -> None:
    compiled = _compiled()
    connection, dispatcher = _dispatcher(tmp_path, compiled)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)

    dispatcher.process_entity(_state("binary_sensor.pump_trip", "on", start), now=start)
    assert dispatcher.alarm_state(CHILD).lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert dispatcher.alarm_inhibited_by(CHILD) == ()

    dispatcher.process_entity(
        _state("binary_sensor.comms_fail", "on", start + timedelta(seconds=1)),
        now=start + timedelta(seconds=1),
    )
    assert dispatcher.alarm_state(BLOCKER).lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert dispatcher.alarm_state(CHILD).lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert dispatcher.alarm_inhibited_by(CHILD) == (BLOCKER,)

    active_ids = {row["alarm_id"] for row in list_alarm_states(connection, view="active")}
    inhibited = list_alarm_states(connection, view="inhibited")
    assert active_ids == {BLOCKER}
    assert [row["alarm_id"] for row in inhibited] == [CHILD]
    assert inhibited[0]["inhibited_by"] == [BLOCKER]

    dispatcher.process_entity(
        _state("binary_sensor.pump_trip", "off", start + timedelta(seconds=2)),
        now=start + timedelta(seconds=2),
    )
    assert dispatcher.alarm_state(CHILD).lifecycle == AlarmLifecycle.NORMAL
    assert dispatcher.alarm_inhibited_by(CHILD) == (BLOCKER,)

    dispatcher.process_entity(
        _state("binary_sensor.comms_fail", "off", start + timedelta(seconds=3)),
        now=start + timedelta(seconds=3),
    )
    assert dispatcher.alarm_inhibited_by(CHILD) == ()
    assert list_alarm_states(connection, view="inhibited") == []

    history = list_alarm_history(connection, alarm_id=CHILD, limit=20)
    control_events = [
        row["event_type"]
        for row in history
        if row["event_type"] in {"INHIBIT", "UNINHIBIT"}
    ]
    assert control_events == ["UNINHIBIT", "INHIBIT"]
    connection.close()


def test_active_inhibitor_creates_inhibited_child_state_before_first_sample(tmp_path: Path) -> None:
    compiled = _compiled()
    connection, dispatcher = _dispatcher(tmp_path, compiled)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)

    dispatcher.process_entity(_state("binary_sensor.comms_fail", "on", start), now=start)

    assert dispatcher.alarm_state(BLOCKER).lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert dispatcher.alarm_state(CHILD).lifecycle == AlarmLifecycle.NORMAL
    assert dispatcher.alarm_inhibited_by(CHILD) == (BLOCKER,)
    stored = connection.execute(
        "SELECT lifecycle, inhibited, inhibited_by_json FROM alarm_state WHERE alarm_id = ?",
        (CHILD,),
    ).fetchone()
    assert stored == (AlarmLifecycle.NORMAL.value, 1, '["COMMS.FAIL"]')
    history = list_alarm_history(connection, alarm_id=CHILD, limit=10)
    assert history[0]["event_type"] == "INHIBIT"
    connection.close()


def test_pending_inhibitor_does_not_hide_child_until_activation(tmp_path: Path) -> None:
    compiled = _compiled(blocker_on_delay_s=10)
    connection, dispatcher = _dispatcher(tmp_path, compiled)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)

    dispatcher.process_entity(_state("binary_sensor.pump_trip", "on", start), now=start)
    dispatcher.process_entity(_state("binary_sensor.comms_fail", "on", start), now=start)

    assert dispatcher.alarm_state(BLOCKER).lifecycle == AlarmLifecycle.PENDING_ON
    assert dispatcher.alarm_inhibited_by(CHILD) == ()

    dispatcher.tick(now=start + timedelta(seconds=10))
    assert dispatcher.alarm_state(BLOCKER).lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert dispatcher.alarm_inhibited_by(CHILD) == (BLOCKER,)
    connection.close()


def test_latched_returned_inhibitor_releases_child(tmp_path: Path) -> None:
    compiled = _compiled(blocker_latching=True)
    connection, dispatcher = _dispatcher(tmp_path, compiled)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)

    dispatcher.process_entity(_state("binary_sensor.pump_trip", "on", start), now=start)
    dispatcher.process_entity(_state("binary_sensor.comms_fail", "on", start), now=start)
    assert dispatcher.alarm_inhibited_by(CHILD) == (BLOCKER,)

    dispatcher.process_entity(
        _state("binary_sensor.comms_fail", "off", start + timedelta(seconds=1)),
        now=start + timedelta(seconds=1),
    )
    blocker = dispatcher.alarm_state(BLOCKER)
    assert blocker.lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert blocker.latched is True
    assert blocker.condition_abnormal is False
    assert dispatcher.alarm_inhibited_by(CHILD) == ()
    connection.close()


def test_inhibition_state_survives_restart(tmp_path: Path) -> None:
    compiled = _compiled()
    connection, dispatcher = _dispatcher(tmp_path, compiled)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)

    dispatcher.process_entity(_state("binary_sensor.pump_trip", "on", start), now=start)
    dispatcher.process_entity(_state("binary_sensor.comms_fail", "on", start), now=start)
    assert dispatcher.alarm_inhibited_by(CHILD) == (BLOCKER,)

    restarted = AlarmDispatcher(compiled, revision_id="rev-inhibit", connection=connection)
    assert restarted.alarm_state(BLOCKER).lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert restarted.alarm_state(CHILD).lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert restarted.alarm_inhibited_by(CHILD) == (BLOCKER,)
    connection.close()
