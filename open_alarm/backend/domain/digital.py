from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import DigitalRule


@dataclass(slots=True)
class DigitalQualifierState:
    raw_alarm: bool = False
    qualified_alarm: bool = False
    pending_target: bool | None = None
    pending_started_at: datetime | None = None
    pending_deadline: datetime | None = None


def qualify_digital(
    state: DigitalQualifierState,
    *,
    raw_alarm: bool,
    rule: DigitalRule,
    now: datetime,
) -> bool:
    """Apply raw-input debounce and return the qualified alarm condition.

    Alarm ON/OFF delays are intentionally not handled here. They belong to the
    alarm lifecycle engine and operate after this debounce layer.
    """
    state.raw_alarm = raw_alarm

    if raw_alarm == state.qualified_alarm:
        state.pending_target = None
        state.pending_started_at = None
        state.pending_deadline = None
        return state.qualified_alarm

    delay_s = rule.debounce_on_s if raw_alarm else rule.debounce_off_s
    if delay_s <= 0:
        state.qualified_alarm = raw_alarm
        state.pending_target = None
        state.pending_started_at = None
        state.pending_deadline = None
        return state.qualified_alarm

    if state.pending_target != raw_alarm:
        state.pending_target = raw_alarm
        state.pending_started_at = now
        state.pending_deadline = now + timedelta(seconds=delay_s)
        return state.qualified_alarm

    if state.pending_deadline is not None and now >= state.pending_deadline:
        state.qualified_alarm = raw_alarm
        state.pending_target = None
        state.pending_started_at = None
        state.pending_deadline = None

    return state.qualified_alarm
