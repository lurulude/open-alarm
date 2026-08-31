from datetime import UTC, datetime, timedelta

from open_alarm.backend.domain.digital import DigitalQualifierState, qualify_digital
from open_alarm.backend.domain.models import DigitalRule


def test_debounce_on_requires_continuous_raw_alarm() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rule = DigitalRule(alarm_value="on", debounce_on_s=2.0)
    state = DigitalQualifierState()

    assert qualify_digital(state, raw_alarm=True, rule=rule, now=start) is False
    assert qualify_digital(
        state,
        raw_alarm=False,
        rule=rule,
        now=start + timedelta(seconds=1),
    ) is False
    assert state.pending_deadline is None

    assert qualify_digital(
        state,
        raw_alarm=True,
        rule=rule,
        now=start + timedelta(seconds=2),
    ) is False
    assert qualify_digital(
        state,
        raw_alarm=True,
        rule=rule,
        now=start + timedelta(seconds=4),
    ) is True


def test_debounce_off_is_independent() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rule = DigitalRule(alarm_value="on", debounce_on_s=0, debounce_off_s=5)
    state = DigitalQualifierState(qualified_alarm=True, raw_alarm=True)

    assert qualify_digital(state, raw_alarm=False, rule=rule, now=start) is True
    assert qualify_digital(
        state,
        raw_alarm=False,
        rule=rule,
        now=start + timedelta(seconds=5),
    ) is False
