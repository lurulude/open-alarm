from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import AlarmEventType, AlarmLifecycle, AlarmPolicy


@dataclass(slots=True)
class AlarmRuntimeState:
    lifecycle: AlarmLifecycle = AlarmLifecycle.NORMAL
    condition_abnormal: bool = False
    acked: bool = False
    latched: bool = False
    active_since: datetime | None = None
    returned_at: datetime | None = None
    pending_started_at: datetime | None = None
    pending_deadline: datetime | None = None
    pending_origin: AlarmLifecycle | None = None


@dataclass(frozen=True, slots=True)
class AlarmEvent:
    event_type: AlarmEventType
    at: datetime


@dataclass(slots=True)
class EngineResult:
    state: AlarmRuntimeState
    events: list[AlarmEvent] = field(default_factory=list)


def _clear_pending(state: AlarmRuntimeState) -> None:
    state.pending_started_at = None
    state.pending_deadline = None
    state.pending_origin = None


def _activate(
    state: AlarmRuntimeState,
    now: datetime,
    policy: AlarmPolicy,
    events: list[AlarmEvent],
) -> None:
    state.lifecycle = AlarmLifecycle.ACTIVE_UNACK
    state.condition_abnormal = True
    state.acked = False
    state.latched = policy.latching
    state.active_since = now
    state.returned_at = None
    _clear_pending(state)
    events.append(AlarmEvent(AlarmEventType.ACTIVATE, now))


def _return_normal(
    state: AlarmRuntimeState,
    now: datetime,
    policy: AlarmPolicy,
    events: list[AlarmEvent],
) -> None:
    origin = state.pending_origin or state.lifecycle
    state.condition_abnormal = False
    state.returned_at = now
    events.append(AlarmEvent(AlarmEventType.RETURN, now))

    if policy.latching and state.latched:
        state.lifecycle = origin
        state.acked = origin == AlarmLifecycle.ACTIVE_ACK
    elif origin == AlarmLifecycle.ACTIVE_UNACK and policy.rtn_ack_required:
        state.lifecycle = AlarmLifecycle.RTN_UNACK
        state.acked = False
    else:
        state.lifecycle = AlarmLifecycle.NORMAL
        state.acked = False
        state.latched = False
        state.active_since = None

    _clear_pending(state)


def process_condition(
    state: AlarmRuntimeState,
    *,
    abnormal: bool,
    policy: AlarmPolicy,
    now: datetime,
) -> EngineResult:
    events: list[AlarmEvent] = []

    if state.lifecycle == AlarmLifecycle.NORMAL:
        state.condition_abnormal = abnormal
        if abnormal:
            if policy.on_delay_s <= 0:
                _activate(state, now, policy, events)
            else:
                state.lifecycle = AlarmLifecycle.PENDING_ON
                state.pending_started_at = now
                state.pending_deadline = now + timedelta(seconds=policy.on_delay_s)
                events.append(AlarmEvent(AlarmEventType.PENDING_ON, now))
        return EngineResult(state, events)

    if state.lifecycle == AlarmLifecycle.PENDING_ON:
        state.condition_abnormal = abnormal
        if not abnormal:
            state.lifecycle = AlarmLifecycle.NORMAL
            _clear_pending(state)
            events.append(AlarmEvent(AlarmEventType.PENDING_CANCEL, now))
        elif state.pending_deadline is not None and now >= state.pending_deadline:
            _activate(state, now, policy, events)
        return EngineResult(state, events)

    if state.lifecycle in (AlarmLifecycle.ACTIVE_UNACK, AlarmLifecycle.ACTIVE_ACK):
        if policy.latching and state.latched and state.returned_at is not None:
            if abnormal:
                state.lifecycle = AlarmLifecycle.ACTIVE_UNACK
                state.condition_abnormal = True
                state.acked = False
                state.returned_at = None
                events.append(AlarmEvent(AlarmEventType.REACTIVATE, now))
            else:
                state.condition_abnormal = False
            return EngineResult(state, events)

        state.condition_abnormal = abnormal
        if not abnormal:
            origin = state.lifecycle
            if policy.off_delay_s <= 0:
                state.pending_origin = origin
                _return_normal(state, now, policy, events)
            else:
                state.lifecycle = AlarmLifecycle.PENDING_OFF
                state.pending_origin = origin
                state.pending_started_at = now
                state.pending_deadline = now + timedelta(seconds=policy.off_delay_s)
                events.append(AlarmEvent(AlarmEventType.PENDING_OFF, now))
        return EngineResult(state, events)

    if state.lifecycle == AlarmLifecycle.PENDING_OFF:
        state.condition_abnormal = abnormal
        if abnormal:
            state.lifecycle = state.pending_origin or AlarmLifecycle.ACTIVE_UNACK
            _clear_pending(state)
            events.append(AlarmEvent(AlarmEventType.RETURN_CANCEL, now))
        elif state.pending_deadline is not None and now >= state.pending_deadline:
            _return_normal(state, now, policy, events)
        return EngineResult(state, events)

    if state.lifecycle == AlarmLifecycle.RTN_UNACK:
        state.condition_abnormal = abnormal
        if abnormal:
            state.lifecycle = AlarmLifecycle.ACTIVE_UNACK
            state.acked = False
            state.active_since = now
            state.returned_at = None
            events.append(AlarmEvent(AlarmEventType.REACTIVATE, now))
        return EngineResult(state, events)

    raise ValueError(f"unsupported lifecycle: {state.lifecycle}")


def acknowledge(state: AlarmRuntimeState, *, now: datetime) -> EngineResult:
    events: list[AlarmEvent] = []

    if state.lifecycle == AlarmLifecycle.ACTIVE_UNACK:
        state.lifecycle = AlarmLifecycle.ACTIVE_ACK
        state.acked = True
        events.append(AlarmEvent(AlarmEventType.ACK, now))
    elif state.lifecycle == AlarmLifecycle.PENDING_OFF:
        if state.pending_origin == AlarmLifecycle.ACTIVE_UNACK:
            state.pending_origin = AlarmLifecycle.ACTIVE_ACK
            state.acked = True
            events.append(AlarmEvent(AlarmEventType.ACK, now))
    elif state.lifecycle == AlarmLifecycle.RTN_UNACK:
        state.lifecycle = AlarmLifecycle.NORMAL
        state.acked = False
        state.latched = False
        state.active_since = None
        events.append(AlarmEvent(AlarmEventType.ACK_RETURN, now))

    return EngineResult(state, events)


def reset_latched(state: AlarmRuntimeState, *, now: datetime) -> EngineResult:
    if not state.latched:
        raise ValueError("alarm is not latched")
    if state.condition_abnormal:
        raise ValueError("alarm condition is still abnormal")
    if state.lifecycle != AlarmLifecycle.ACTIVE_ACK:
        raise ValueError("latched alarm must be acknowledged before reset")
    if state.returned_at is None:
        raise ValueError("latched alarm has not returned to normal")

    state.lifecycle = AlarmLifecycle.NORMAL
    state.acked = False
    state.latched = False
    state.active_since = None
    _clear_pending(state)
    return EngineResult(state, [AlarmEvent(AlarmEventType.RESET, now)])
