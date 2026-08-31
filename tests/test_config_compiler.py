from dataclasses import replace

from open_alarm.backend.config.compiler import compile_config
from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    NotificationPolicyDefinition,
    TagDefinition,
)


def _valid_tags() -> list[TagDefinition]:
    return [
        TagDefinition("TEMP_01", "sensor.temp_01", stale_after_s=120),
        TagDefinition("DOOR_01", "binary_sensor.door_01"),
    ]


def _valid_alarms() -> list[AlarmDefinition]:
    return [
        AlarmDefinition(
            alarm_id="TEMP_01.HI",
            source_tag_id="TEMP_01",
            kind=AlarmKind.ANALOG,
            condition="HIGH",
            priority="P2",
            category="PROCESS",
            setpoint=85.0,
            hysteresis=2.0,
            on_delay_s=5,
            off_delay_s=10,
        ),
        AlarmDefinition(
            alarm_id="DOOR_01.OPEN",
            source_tag_id="DOOR_01",
            kind=AlarmKind.DIGITAL,
            condition="EQUALS",
            priority="P3",
            category="SECURITY",
            alarm_value="on",
            debounce_on_s=0.5,
            debounce_off_s=1.0,
            on_delay_s=2,
            off_delay_s=3,
            inhibit_by_alarm_ids=("TEMP_01.HI",),
        ),
    ]


def test_valid_configuration_compiles_with_stable_hash() -> None:
    tags = _valid_tags()
    alarms = _valid_alarms()
    kwargs = {
        "known_entity_ids": {"sensor.temp_01", "binary_sensor.door_01"},
        "priorities": {"P2", "P3"},
        "categories": {"PROCESS", "SECURITY"},
    }

    first = compile_config(tags=tags, alarms=alarms, **kwargs)
    second = compile_config(tags=reversed(tags), alarms=reversed(alarms), **kwargs)

    assert first.ok is True
    assert second.ok is True
    assert first.compiled is not None
    assert second.compiled is not None
    assert first.compiled.source_hash == second.compiled.source_hash
    door = next(alarm for alarm in first.compiled.alarms if alarm.alarm_id == "DOOR_01.OPEN")
    assert door.inhibit_by_alarm_ids == ("TEMP_01.HI",)


def test_notification_policy_is_validated_and_hashed() -> None:
    alarm = replace(_valid_alarms()[0], notification_policy_id="P1_PHONE")
    policy = NotificationPolicyDefinition(
        policy_id="P1_PHONE",
        route_key="notify.mobile_app_phone",
        notify_on_return=True,
        notify_on_ack=True,
        notify_delay_s=15,
        notification_channel="open_alarm_p1",
        critical=True,
    )

    first = compile_config(tags=_valid_tags(), alarms=[alarm], notification_policies=[policy])
    second = compile_config(tags=_valid_tags(), alarms=[alarm], notification_policies=[policy])
    changed = compile_config(
        tags=_valid_tags(),
        alarms=[alarm],
        notification_policies=[replace(policy, notify_delay_s=30)],
    )

    assert first.ok is True
    assert second.ok is True
    assert changed.ok is True
    assert first.compiled is not None
    assert second.compiled is not None
    assert changed.compiled is not None
    assert first.compiled.notification_policies == (policy,)
    assert first.compiled.source_hash == second.compiled.source_hash
    assert first.compiled.source_hash != changed.compiled.source_hash


def test_missing_notification_policy_rejects_revision() -> None:
    alarm = replace(_valid_alarms()[0], notification_policy_id="MISSING_POLICY")

    result = compile_config(tags=_valid_tags(), alarms=[alarm])

    assert result.ok is False
    assert "NOTIFICATION_POLICY_NOT_FOUND" in {issue.code for issue in result.issues}


def test_invalid_notification_route_and_delay_reject_revision() -> None:
    policies = [
        NotificationPolicyDefinition(
            policy_id="BAD_ROUTE",
            route_key="mobile_app_phone",
        ),
        NotificationPolicyDefinition(
            policy_id="BAD_DELAY",
            route_key="notify.mobile_app_phone",
            notify_delay_s=-1,
        ),
    ]

    result = compile_config(tags=_valid_tags(), alarms=_valid_alarms(), notification_policies=policies)

    codes = {issue.code for issue in result.issues}
    assert result.ok is False
    assert "INVALID_NOTIFICATION_ROUTE" in codes
    assert "INVALID_NOTIFICATION_DELAY" in codes


def test_missing_home_assistant_entity_rejects_revision() -> None:
    result = compile_config(
        tags=_valid_tags(),
        alarms=_valid_alarms(),
        known_entity_ids={"sensor.temp_01"},
    )

    assert result.ok is False
    assert result.compiled is None
    assert "ENTITY_NOT_FOUND" in {issue.code for issue in result.issues}


def test_negative_alarm_delay_rejects_revision() -> None:
    bad_alarm = AlarmDefinition(
        alarm_id="DOOR_01.OPEN",
        source_tag_id="DOOR_01",
        kind=AlarmKind.DIGITAL,
        condition="EQUALS",
        priority="P3",
        category="SECURITY",
        alarm_value="on",
        on_delay_s=-1,
    )
    result = compile_config(tags=_valid_tags(), alarms=[bad_alarm])

    assert result.ok is False
    assert "INVALID_DELAY" in {issue.code for issue in result.issues}


def test_duplicate_alarm_id_rejects_whole_revision() -> None:
    alarms = _valid_alarms()
    result = compile_config(tags=_valid_tags(), alarms=[alarms[0], alarms[0]])

    assert result.ok is False
    assert result.compiled is None
    assert "DUPLICATE_ALARM_ID" in {issue.code for issue in result.issues}


def test_missing_inhibitor_rejects_revision() -> None:
    alarms = _valid_alarms()
    bad = replace(alarms[1], inhibit_by_alarm_ids=("MISSING.ALARM",))

    result = compile_config(tags=_valid_tags(), alarms=[alarms[0], bad])

    assert result.ok is False
    assert "INHIBITOR_NOT_FOUND" in {issue.code for issue in result.issues}


def test_disabled_inhibitor_rejects_revision() -> None:
    alarms = _valid_alarms()
    disabled = replace(alarms[0], enabled=False)

    result = compile_config(tags=_valid_tags(), alarms=[disabled, alarms[1]])

    assert result.ok is False
    assert "INHIBITOR_DISABLED" in {issue.code for issue in result.issues}


def test_inhibition_cycle_rejects_revision() -> None:
    alarms = _valid_alarms()
    temp = replace(alarms[0], inhibit_by_alarm_ids=(alarms[1].alarm_id,))
    door = replace(alarms[1], inhibit_by_alarm_ids=(alarms[0].alarm_id,))

    result = compile_config(tags=_valid_tags(), alarms=[temp, door])

    assert result.ok is False
    cycle_issues = [issue for issue in result.issues if issue.code == "INHIBITION_CYCLE"]
    assert {issue.object_id for issue in cycle_issues} == {"TEMP_01.HI", "DOOR_01.OPEN"}
