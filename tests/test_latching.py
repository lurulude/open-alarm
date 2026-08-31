from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    TagDefinition,
)
from open_alarm.backend.db.alarm_query_repository import list_alarm_history
from open_alarm.backend.db.config_repository import store_compiled_revision
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.domain.engine import (
    AlarmRuntimeState,
    acknowledge,
    process_condition,
    reset_latched,
)
from open_alarm.backend.domain.models import AlarmEventType, AlarmLifecycle, AlarmPolicy
from open_alarm.backend.ha.models import normalize_entity_state
from open_alarm.backend.runtime.dispatcher import AlarmDispatcher


def _state(value: str, at: datetime):
    return normalize_entity_state(
        "sensor.temp",
        {"state": value, "attributes": {}},
        observed_at=at,
        source_timestamp=at,
    )


def _compiled() -> CompiledConfig:
    return CompiledConfig(
        schema_version="1.0.0",
        source_hash="latching-test",
        tags=(TagDefinition("TEMP", "sensor.temp"),),
        alarms=(
            AlarmDefinition(
                alarm_id="TEMP_HI_LATCH",
                source_tag_id="TEMP",
                kind=AlarmKind.ANALOG,
                condition="HIGH",
                priority="P1",
                category="PROCESS",
                setpoint=80.0,
                hysteresis=2.0,
                latching=True,
            ),
        ),
    )


def test_latched_alarm_returns_but_requires_ack_and_reset() -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    policy = AlarmPolicy(latching=True)
    state = AlarmRuntimeState()

    activated = process_condition(state, abnormal=True, policy=policy, now=now)
    assert activated.state.lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert activated.state.latched is True

    returned = process_condition(
        state,
        abnormal=False,
        policy=policy,
        now=now + timedelta(seconds=1),
    )
    assert returned.state.lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert returned.state.condition_abnormal is False
    assert returned.state.latched is True
    assert [event.event_type for event in returned.events] == [AlarmEventType.RETURN]

    repeated = process_condition(
        state,
        abnormal=False,
        policy=policy,
        now=now + timedelta(seconds=2),
    )
    assert repeated.events == []

    with pytest.raises(ValueError, match="acknowledged"):
        reset_latched(state, now=now + timedelta(seconds=3))

    acknowledge(state, now=now + timedelta(seconds=3))
    reset = reset_latched(state, now=now + timedelta(seconds=4))
    assert reset.state.lifecycle == AlarmLifecycle.NORMAL
    assert reset.state.latched is False
    assert [event.event_type for event in reset.events] == [AlarmEventType.RESET]


def test_latched_alarm_cannot_reset_while_abnormal() -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    state = AlarmRuntimeState(
        lifecycle=AlarmLifecycle.ACTIVE_ACK,
        condition_abnormal=True,
        acked=True,
        latched=True,
        active_since=now,
    )
    with pytest.raises(ValueError, match="still abnormal"):
        reset_latched(state, now=now)


def test_latched_alarm_reactivation_requires_new_ack() -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    policy = AlarmPolicy(latching=True)
    state = AlarmRuntimeState()
    process_condition(state, abnormal=True, policy=policy, now=now)
    acknowledge(state, now=now + timedelta(seconds=1))
    process_condition(state, abnormal=False, policy=policy, now=now + timedelta(seconds=2))

    result = process_condition(
        state,
        abnormal=True,
        policy=policy,
        now=now + timedelta(seconds=3),
    )
    assert result.state.lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert result.state.acked is False
    assert result.state.returned_at is None
    assert [event.event_type for event in result.events] == [AlarmEventType.REACTIVATE]


def test_latching_conflicts_with_return_ack() -> None:
    with pytest.raises(ValueError, match="cannot both"):
        AlarmPolicy(latching=True, rtn_ack_required=True)
    with pytest.raises(ValueError, match="cannot both"):
        AlarmDefinition(
            alarm_id="BAD",
            source_tag_id="TEMP",
            kind=AlarmKind.ANALOG,
            condition="HIGH",
            priority="P1",
            category="PROCESS",
            setpoint=80,
            latching=True,
            rtn_ack_required=True,
        )


def test_latched_state_survives_restart_and_reset_is_persisted(tmp_path: Path) -> None:
    connection = connect(tmp_path / "latching.db")
    apply_migrations(connection)
    compiled = _compiled()
    store_compiled_revision(connection, compiled, revision_id="rev-latch")
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)

    dispatcher = AlarmDispatcher(compiled, revision_id="rev-latch", connection=connection)
    dispatcher.process_entity(_state("90", start), now=start)
    dispatcher.process_entity(
        _state("70", start + timedelta(seconds=1)),
        now=start + timedelta(seconds=1),
    )
    dispatcher.acknowledge(
        "TEMP_HI_LATCH",
        user_id="operator",
        now=start + timedelta(seconds=2),
    )

    restarted = AlarmDispatcher(compiled, revision_id="rev-latch", connection=connection)
    restored = restarted.alarm_state("TEMP_HI_LATCH")
    assert restored.lifecycle == AlarmLifecycle.ACTIVE_ACK
    assert restored.condition_abnormal is False
    assert restored.latched is True
    assert restored.returned_at == start + timedelta(seconds=1)

    restarted.reset(
        "TEMP_HI_LATCH",
        user_id="operator",
        now=start + timedelta(seconds=3),
    )
    restarted_again = AlarmDispatcher(compiled, revision_id="rev-latch", connection=connection)
    final = restarted_again.alarm_state("TEMP_HI_LATCH")
    assert final.lifecycle == AlarmLifecycle.NORMAL
    assert final.latched is False

    history = list_alarm_history(connection, alarm_id="TEMP_HI_LATCH", limit=10)
    assert history[0]["event_type"] == "RESET"
    assert history[0]["user_id"] == "operator"
    connection.close()
