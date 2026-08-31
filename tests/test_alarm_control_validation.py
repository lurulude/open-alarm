from open_alarm.backend.api.alarm_controls import ControlReason


def test_control_reason_allows_blank_value() -> None:
    assert ControlReason(reason="   ").reason is None
    assert ControlReason().reason is None


def test_control_reason_is_trimmed_before_persistence() -> None:
    assert ControlReason(reason="  maintenance  ").reason == "maintenance"
