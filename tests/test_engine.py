from datetime import UTC, datetime, timedelta

from open_alarm.backend.domain.engine import AlarmRuntimeState, acknowledge, process_condition
from open_alarm.backend.domain.models import AlarmEventType, AlarmLifecycle, AlarmPolicy


def test_on_delay_must_be_continuous() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    state = AlarmRuntimeState()
    policy = AlarmPolicy(on_delay_s=10)

    result = process_condition(state, abnormal=True, policy=policy, now=start)
    assert result.state.lifecycle == AlarmLifecycle.PENDING_ON
    assert result.events[0].event_type == AlarmEventType.PENDING_ON

    result = process_condition(
        state,
        abnormal=False,
        policy=policy,
        now=start + timedelta(seconds=7),
    )
    assert result.state.lifecycle == AlarmLifecycle.NORMAL
    assert result.events[0].event_type == AlarmEventType.PENDING_CANCEL

    result = process_condition(
        state,
        abnormal=True,
        policy=policy,
        now=start + timedelta(seconds=8),
    )
    assert result.state.pending_deadline == start + timedelta(seconds=18)


def test_on_delay_activates_at_deadline() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    state = AlarmRuntimeState()
    policy = AlarmPolicy(on_delay_s=10)

    process_condition(state, abnormal=True, policy=policy, now=start)
    result = process_condition(
        state,
        abnormal=True,
        policy=policy,
        now=start + timedelta(seconds=10),
    )

    assert result.state.lifecycle == AlarmLifecycle.ACTIVE_UNACK
    assert result.events[0].event_type == AlarmEventType.ACTIVATE


def test_off_delay_reversal_keeps_acknowledged_alarm_active() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    state = AlarmRuntimeState(lifecycle=AlarmLifecycle.ACTIVE_ACK, condition_abnormal=True, acked=True)
    policy = AlarmPolicy(off_delay_s=20)

    process_condition(state, abnormal=False, policy=policy, now=start)
    result = process_condition(
        state,
        abnormal=True,
        policy=policy,
        now=start + timedelta(seconds=12),
    )

    assert result.state.lifecycle == AlarmLifecycle.ACTIVE_ACK
    assert result.events[0].event_type == AlarmEventType.RETURN_CANCEL


def test_return_before_ack_can_require_return_ack() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state = AlarmRuntimeState(lifecycle=AlarmLifecycle.ACTIVE_UNACK, condition_abnormal=True)
    policy = AlarmPolicy(rtn_ack_required=True)

    result = process_condition(state, abnormal=False, policy=policy, now=now)
    assert result.state.lifecycle == AlarmLifecycle.RTN_UNACK
    assert result.events[0].event_type == AlarmEventType.RETURN

    result = acknowledge(state, now=now + timedelta(seconds=1))
    assert result.state.lifecycle == AlarmLifecycle.NORMAL
    assert result.events[0].event_type == AlarmEventType.ACK_RETURN


def test_ack_during_pending_off_changes_return_outcome() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    state = AlarmRuntimeState(lifecycle=AlarmLifecycle.ACTIVE_UNACK, condition_abnormal=True)
    policy = AlarmPolicy(off_delay_s=10, rtn_ack_required=True)

    process_condition(state, abnormal=False, policy=policy, now=start)
    acknowledge(state, now=start + timedelta(seconds=2))
    result = process_condition(
        state,
        abnormal=False,
        policy=policy,
        now=start + timedelta(seconds=10),
    )

    assert result.state.lifecycle == AlarmLifecycle.NORMAL
