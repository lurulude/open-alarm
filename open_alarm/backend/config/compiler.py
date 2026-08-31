from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from dataclasses import asdict
from enum import Enum
from typing import Any

from ..domain.models import AnalogCondition
from .models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    CompileResult,
    IssueSeverity,
    NotificationPolicyDefinition,
    TagDefinition,
    ValidationIssue,
)

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
NOTIFY_ROUTE_PATTERN = re.compile(r"^notify\.[a-z0-9_]+$")
DIGITAL_CONDITIONS = frozenset({"EQUALS", "NOT_EQUALS"})
DEVICE_CONDITIONS = frozenset({"UNAVAILABLE", "UNKNOWN", "MISSING", "STALE", "BAD_QUALITY"})


def compile_config(
    *,
    tags: Iterable[TagDefinition],
    alarms: Iterable[AlarmDefinition],
    notification_policies: Iterable[NotificationPolicyDefinition] = (),
    known_entity_ids: Iterable[str] | None = None,
    priorities: Iterable[str] | None = None,
    categories: Iterable[str] | None = None,
    schema_version: str = "1.0.0",
) -> CompileResult:
    tag_list = tuple(tags)
    alarm_list = tuple(alarms)
    policy_list = tuple(notification_policies)
    known_entities = None if known_entity_ids is None else frozenset(known_entity_ids)
    valid_priorities = None if priorities is None else frozenset(priorities)
    valid_categories = None if categories is None else frozenset(categories)

    issues: list[ValidationIssue] = []
    issues.extend(_validate_tags(tag_list, known_entities))
    issues.extend(_validate_notification_policies(policy_list))
    issues.extend(
        _validate_alarms(
            alarm_list,
            tag_enabled_by_id={tag.tag_id: tag.enabled for tag in tag_list},
            policy_enabled_by_id={policy.policy_id: policy.enabled for policy in policy_list},
            priorities=valid_priorities,
            categories=valid_categories,
        )
    )
    issues.extend(_validate_inhibition_graph(alarm_list))

    if any(issue.severity == IssueSeverity.ERROR for issue in issues):
        return CompileResult(issues=tuple(issues), compiled=None)

    sorted_tags = tuple(sorted(tag_list, key=lambda tag: tag.tag_id))
    sorted_alarms = tuple(sorted(alarm_list, key=lambda alarm: alarm.alarm_id))
    sorted_policies = tuple(sorted(policy_list, key=lambda policy: policy.policy_id))
    source_hash = _config_hash(schema_version, sorted_tags, sorted_alarms, sorted_policies)
    return CompileResult(
        issues=tuple(issues),
        compiled=CompiledConfig(
            schema_version=schema_version,
            source_hash=source_hash,
            tags=sorted_tags,
            alarms=sorted_alarms,
            notification_policies=sorted_policies,
        ),
    )


def _validate_tags(
    tags: tuple[TagDefinition, ...],
    known_entity_ids: frozenset[str] | None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: set[str] = set()

    for tag in tags:
        if tag.tag_id in seen:
            issues.append(
                _error(
                    "DUPLICATE_TAG_ID",
                    "duplicate Tag ID",
                    "tag",
                    tag.tag_id,
                    "tag_id",
                )
            )
        seen.add(tag.tag_id)

        if not ID_PATTERN.fullmatch(tag.tag_id):
            issues.append(
                _error(
                    "INVALID_TAG_ID",
                    "Tag ID has an invalid format",
                    "tag",
                    tag.tag_id,
                    "tag_id",
                )
            )
        if not ENTITY_ID_PATTERN.fullmatch(tag.entity_id):
            issues.append(
                _error(
                    "INVALID_ENTITY_ID",
                    "Home Assistant entity_id has an invalid format",
                    "tag",
                    tag.tag_id,
                    "entity_id",
                )
            )
        if tag.stale_after_s is not None and (
            not math.isfinite(tag.stale_after_s) or tag.stale_after_s < 0
        ):
            issues.append(
                _error(
                    "INVALID_STALE_DELAY",
                    "stale_after_s must be a finite value >= 0",
                    "tag",
                    tag.tag_id,
                    "stale_after_s",
                )
            )
        if tag.enabled and known_entity_ids is not None and tag.entity_id not in known_entity_ids:
            issues.append(
                _error(
                    "ENTITY_NOT_FOUND",
                    "configured Home Assistant entity was not found",
                    "tag",
                    tag.tag_id,
                    "entity_id",
                )
            )

    return issues


def _validate_notification_policies(
    policies: tuple[NotificationPolicyDefinition, ...],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: set[str] = set()

    for policy in policies:
        if policy.policy_id in seen:
            issues.append(
                _error(
                    "DUPLICATE_NOTIFICATION_POLICY_ID",
                    "duplicate Notification Policy ID",
                    "notification_policy",
                    policy.policy_id,
                    "policy_id",
                )
            )
        seen.add(policy.policy_id)

        if not ID_PATTERN.fullmatch(policy.policy_id):
            issues.append(
                _error(
                    "INVALID_NOTIFICATION_POLICY_ID",
                    "Notification Policy ID has an invalid format",
                    "notification_policy",
                    policy.policy_id,
                    "policy_id",
                )
            )
        if not NOTIFY_ROUTE_PATTERN.fullmatch(policy.route_key):
            issues.append(
                _error(
                    "INVALID_NOTIFICATION_ROUTE",
                    "route_key must name a Home Assistant notify action such as notify.mobile_app_phone",
                    "notification_policy",
                    policy.policy_id,
                    "route_key",
                )
            )
        if not math.isfinite(policy.notify_delay_s) or policy.notify_delay_s < 0:
            issues.append(
                _error(
                    "INVALID_NOTIFICATION_DELAY",
                    "notify_delay_s must be a finite value >= 0",
                    "notification_policy",
                    policy.policy_id,
                    "notify_delay_s",
                )
            )
        for field_name in ("notification_channel", "notification_group"):
            value = getattr(policy, field_name)
            if value is not None and not value.strip():
                issues.append(
                    _error(
                        "INVALID_NOTIFICATION_FIELD",
                        f"{field_name} must be null or a non-empty string",
                        "notification_policy",
                        policy.policy_id,
                        field_name,
                    )
                )

    return issues


def _validate_alarms(
    alarms: tuple[AlarmDefinition, ...],
    *,
    tag_enabled_by_id: dict[str, bool],
    policy_enabled_by_id: dict[str, bool],
    priorities: frozenset[str] | None,
    categories: frozenset[str] | None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    alarm_ids = {alarm.alarm_id for alarm in alarms}
    enabled_by_id = {alarm.alarm_id: alarm.enabled for alarm in alarms}

    for alarm in alarms:
        if alarm.alarm_id in seen:
            issues.append(
                _error(
                    "DUPLICATE_ALARM_ID",
                    "duplicate Alarm ID",
                    "alarm",
                    alarm.alarm_id,
                    "alarm_id",
                )
            )
        seen.add(alarm.alarm_id)

        if not ID_PATTERN.fullmatch(alarm.alarm_id):
            issues.append(
                _error(
                    "INVALID_ALARM_ID",
                    "Alarm ID has an invalid format",
                    "alarm",
                    alarm.alarm_id,
                    "alarm_id",
                )
            )
        if alarm.alarm_group_id is not None and not ID_PATTERN.fullmatch(alarm.alarm_group_id):
            issues.append(
                _error(
                    "INVALID_ALARM_GROUP_ID",
                    "Alarm Group ID has an invalid format",
                    "alarm",
                    alarm.alarm_id,
                    "alarm_group_id",
                )
            )
        if alarm.source_tag_id not in tag_enabled_by_id:
            issues.append(
                _error(
                    "SOURCE_TAG_NOT_FOUND",
                    "alarm references an unknown Tag ID",
                    "alarm",
                    alarm.alarm_id,
                    "source_tag_id",
                )
            )
        elif alarm.enabled and not tag_enabled_by_id[alarm.source_tag_id]:
            issues.append(
                _error(
                    "SOURCE_TAG_DISABLED",
                    "enabled alarm references a disabled Tag",
                    "alarm",
                    alarm.alarm_id,
                    "source_tag_id",
                )
            )
        if priorities is not None and alarm.priority not in priorities:
            issues.append(
                _error(
                    "PRIORITY_NOT_FOUND",
                    "alarm references an unknown priority",
                    "alarm",
                    alarm.alarm_id,
                    "priority",
                )
            )
        if categories is not None and alarm.category not in categories:
            issues.append(
                _error(
                    "CATEGORY_NOT_FOUND",
                    "alarm references an unknown category",
                    "alarm",
                    alarm.alarm_id,
                    "category",
                )
            )
        if alarm.notification_policy_id is not None:
            if alarm.notification_policy_id not in policy_enabled_by_id:
                issues.append(
                    _error(
                        "NOTIFICATION_POLICY_NOT_FOUND",
                        "alarm references an unknown Notification Policy ID",
                        "alarm",
                        alarm.alarm_id,
                        "notification_policy_id",
                    )
                )
            elif alarm.enabled and not policy_enabled_by_id[alarm.notification_policy_id]:
                issues.append(
                    _error(
                        "NOTIFICATION_POLICY_DISABLED",
                        "enabled alarm references a disabled Notification Policy",
                        "alarm",
                        alarm.alarm_id,
                        "notification_policy_id",
                    )
                )

        if len(set(alarm.inhibit_by_alarm_ids)) != len(alarm.inhibit_by_alarm_ids):
            issues.append(
                _error(
                    "DUPLICATE_INHIBITOR",
                    "inhibit_by_alarm_ids contains duplicate Alarm IDs",
                    "alarm",
                    alarm.alarm_id,
                    "inhibit_by_alarm_ids",
                )
            )
        for inhibitor_id in alarm.inhibit_by_alarm_ids:
            if inhibitor_id == alarm.alarm_id:
                issues.append(
                    _error(
                        "SELF_INHIBITION",
                        "alarm cannot inhibit itself",
                        "alarm",
                        alarm.alarm_id,
                        "inhibit_by_alarm_ids",
                    )
                )
            elif inhibitor_id not in alarm_ids:
                issues.append(
                    _error(
                        "INHIBITOR_NOT_FOUND",
                        f"inhibitor alarm does not exist: {inhibitor_id}",
                        "alarm",
                        alarm.alarm_id,
                        "inhibit_by_alarm_ids",
                    )
                )
            elif not enabled_by_id.get(inhibitor_id, False):
                issues.append(
                    _error(
                        "INHIBITOR_DISABLED",
                        f"inhibitor alarm is disabled: {inhibitor_id}",
                        "alarm",
                        alarm.alarm_id,
                        "inhibit_by_alarm_ids",
                    )
                )

        issues.extend(_validate_timing(alarm))
        if alarm.kind == AlarmKind.ANALOG:
            issues.extend(_validate_analog(alarm))
        elif alarm.kind == AlarmKind.DIGITAL:
            issues.extend(_validate_digital(alarm))
        elif alarm.kind == AlarmKind.DEVICE:
            issues.extend(_validate_device(alarm))
        else:
            issues.append(
                _error(
                    "INVALID_ALARM_KIND",
                    "unsupported alarm kind",
                    "alarm",
                    alarm.alarm_id,
                    "kind",
                )
            )

    return issues


def _validate_inhibition_graph(alarms: tuple[AlarmDefinition, ...]) -> list[ValidationIssue]:
    graph = {
        alarm.alarm_id: tuple(
            inhibitor_id
            for inhibitor_id in alarm.inhibit_by_alarm_ids
            if inhibitor_id != alarm.alarm_id
        )
        for alarm in alarms
    }
    known_ids = set(graph)
    visited: set[str] = set()
    path: list[str] = []
    path_index: dict[str, int] = {}
    cycles: set[tuple[str, ...]] = set()

    def visit(alarm_id: str) -> None:
        if alarm_id in path_index:
            cycle = tuple(sorted(path[path_index[alarm_id] :]))
            if cycle:
                cycles.add(cycle)
            return
        if alarm_id in visited:
            return

        path_index[alarm_id] = len(path)
        path.append(alarm_id)
        for inhibitor_id in graph.get(alarm_id, ()):
            if inhibitor_id in known_ids:
                visit(inhibitor_id)
        path.pop()
        path_index.pop(alarm_id, None)
        visited.add(alarm_id)

    for alarm_id in graph:
        visit(alarm_id)

    issues: list[ValidationIssue] = []
    for cycle in sorted(cycles):
        cycle_text = " -> ".join((*cycle, cycle[0]))
        for alarm_id in cycle:
            issues.append(
                _error(
                    "INHIBITION_CYCLE",
                    f"alarm inhibition graph contains a cycle: {cycle_text}",
                    "alarm",
                    alarm_id,
                    "inhibit_by_alarm_ids",
                )
            )
    return issues


def _validate_timing(alarm: AlarmDefinition) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field_name in ("debounce_on_s", "debounce_off_s", "on_delay_s", "off_delay_s"):
        value = getattr(alarm, field_name)
        if not math.isfinite(value) or value < 0:
            issues.append(
                _error(
                    "INVALID_DELAY",
                    f"{field_name} must be a finite value >= 0",
                    "alarm",
                    alarm.alarm_id,
                    field_name,
                )
            )
    return issues


def _validate_analog(alarm: AlarmDefinition) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    valid_conditions = {condition.value for condition in AnalogCondition}
    if alarm.condition not in valid_conditions:
        issues.append(
            _error(
                "INVALID_ANALOG_CONDITION",
                "unsupported analog alarm condition",
                "alarm",
                alarm.alarm_id,
                "condition",
            )
        )
    if alarm.setpoint is None or not math.isfinite(alarm.setpoint):
        issues.append(
            _error(
                "INVALID_SETPOINT",
                "analog alarm requires a finite setpoint",
                "alarm",
                alarm.alarm_id,
                "setpoint",
            )
        )
    if not math.isfinite(alarm.hysteresis) or alarm.hysteresis < 0:
        issues.append(
            _error(
                "INVALID_HYSTERESIS",
                "hysteresis must be a finite value >= 0",
                "alarm",
                alarm.alarm_id,
                "hysteresis",
            )
        )
    if alarm.debounce_on_s != 0 or alarm.debounce_off_s != 0:
        issues.append(
            _error(
                "ANALOG_DEBOUNCE_NOT_ALLOWED",
                "analog alarms do not use binary debounce",
                "alarm",
                alarm.alarm_id,
            )
        )
    return issues


def _validate_digital(alarm: AlarmDefinition) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if alarm.condition not in DIGITAL_CONDITIONS:
        issues.append(
            _error(
                "INVALID_DIGITAL_CONDITION",
                "digital condition must be EQUALS or NOT_EQUALS",
                "alarm",
                alarm.alarm_id,
                "condition",
            )
        )
    if alarm.alarm_value is None or alarm.alarm_value == "":
        issues.append(
            _error(
                "MISSING_ALARM_VALUE",
                "digital alarm requires alarm_value",
                "alarm",
                alarm.alarm_id,
                "alarm_value",
            )
        )
    if alarm.hysteresis != 0:
        issues.append(
            _error(
                "DIGITAL_HYSTERESIS_NOT_ALLOWED",
                "digital alarms do not use hysteresis",
                "alarm",
                alarm.alarm_id,
                "hysteresis",
            )
        )
    return issues


def _validate_device(alarm: AlarmDefinition) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if alarm.condition not in DEVICE_CONDITIONS:
        issues.append(
            _error(
                "INVALID_DEVICE_CONDITION",
                "unsupported device/quality alarm condition",
                "alarm",
                alarm.alarm_id,
                "condition",
            )
        )
    if alarm.debounce_on_s != 0 or alarm.debounce_off_s != 0:
        issues.append(
            _error(
                "DEVICE_DEBOUNCE_NOT_ALLOWED",
                "device/quality alarms use ON/OFF delays, not binary debounce",
                "alarm",
                alarm.alarm_id,
            )
        )
    return issues


def _error(
    code: str,
    message: str,
    object_type: str,
    object_id: str,
    field: str | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        severity=IssueSeverity.ERROR,
        code=code,
        message=message,
        object_type=object_type,
        object_id=object_id,
        field=field,
    )


def _config_hash(
    schema_version: str,
    tags: tuple[TagDefinition, ...],
    alarms: tuple[AlarmDefinition, ...],
    notification_policies: tuple[NotificationPolicyDefinition, ...],
) -> str:
    payload = {
        "schema_version": schema_version,
        "tags": [_json_ready(asdict(tag)) for tag in tags],
        "alarms": [_json_ready(asdict(alarm)) for alarm in alarms],
        "notification_policies": [
            _json_ready(asdict(policy)) for policy in notification_policies
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
