from open_alarm.backend.domain.evaluation import analog_is_abnormal
from open_alarm.backend.domain.models import AnalogCondition, AnalogRule


def test_high_alarm_hysteresis() -> None:
    rule = AnalogRule(AnalogCondition.HIGH, setpoint=85.0, hysteresis=2.0)

    assert analog_is_abnormal(rule, 84.9, False) is False
    assert analog_is_abnormal(rule, 85.0, False) is True
    assert analog_is_abnormal(rule, 84.0, True) is True
    assert analog_is_abnormal(rule, 83.0, True) is False


def test_low_alarm_hysteresis() -> None:
    rule = AnalogRule(AnalogCondition.LOW, setpoint=15.0, hysteresis=2.0)

    assert analog_is_abnormal(rule, 15.1, False) is False
    assert analog_is_abnormal(rule, 15.0, False) is True
    assert analog_is_abnormal(rule, 16.0, True) is True
    assert analog_is_abnormal(rule, 17.0, True) is False
