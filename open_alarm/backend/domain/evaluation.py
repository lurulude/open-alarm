from __future__ import annotations

from .models import AnalogCondition, AnalogRule


def analog_is_abnormal(rule: AnalogRule, value: float, previously_abnormal: bool) -> bool:
    """Evaluate an analog condition with hysteresis.

    Hysteresis affects only the return threshold. Pending timing is handled by
    the state machine/timer layer, not by this pure function.
    """
    if rule.condition in (AnalogCondition.HIGH, AnalogCondition.HIGH_HIGH):
        if previously_abnormal:
            return value > (rule.setpoint - rule.hysteresis)
        return value >= rule.setpoint

    if rule.condition in (AnalogCondition.LOW, AnalogCondition.LOW_LOW):
        if previously_abnormal:
            return value < (rule.setpoint + rule.hysteresis)
        return value <= rule.setpoint

    raise ValueError(f"unsupported analog condition: {rule.condition}")
