from dataclasses import replace

from open_alarm.backend.config.compiler import compile_config
from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    NotificationPolicyDefinition,
    TagDefinition,
)


def _alarm(**changes: object) -> AlarmDefinition:
    alarm = AlarmDefinition(
        alarm_id="TEMP.HI",
        source_tag_id="TEMP",
        kind=AlarmKind.ANALOG,
        condition="HIGH",
        priority="P2",
        category="PROCESS",
        setpoint=80.0,
    )
    return replace(alarm, **changes)


def test_enabled_alarm_cannot_reference_disabled_tag() -> None:
    result = compile_config(
        tags=[TagDefinition("TEMP", "sensor.temp", enabled=False)],
        alarms=[_alarm()],
    )

    assert result.ok is False
    assert "SOURCE_TAG_DISABLED" in {issue.code for issue in result.issues}


def test_disabled_alarm_may_reference_disabled_tag() -> None:
    result = compile_config(
        tags=[TagDefinition("TEMP", "sensor.temp", enabled=False)],
        alarms=[_alarm(enabled=False)],
    )

    assert result.ok is True


def test_enabled_alarm_cannot_reference_disabled_notification_policy() -> None:
    result = compile_config(
        tags=[TagDefinition("TEMP", "sensor.temp")],
        alarms=[_alarm(notification_policy_id="PHONE")],
        notification_policies=[
            NotificationPolicyDefinition(
                policy_id="PHONE",
                route_key="notify.mobile_app_phone",
                enabled=False,
            )
        ],
    )

    assert result.ok is False
    assert "NOTIFICATION_POLICY_DISABLED" in {issue.code for issue in result.issues}


def test_disabled_alarm_may_reference_disabled_notification_policy() -> None:
    result = compile_config(
        tags=[TagDefinition("TEMP", "sensor.temp")],
        alarms=[_alarm(notification_policy_id="PHONE", enabled=False)],
        notification_policies=[
            NotificationPolicyDefinition(
                policy_id="PHONE",
                route_key="notify.mobile_app_phone",
                enabled=False,
            )
        ],
    )

    assert result.ok is True
