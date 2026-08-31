from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from enum import Enum
from sqlite3 import Connection
from typing import Any

from ..config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    NotificationPolicyDefinition,
    TagDefinition,
)


def store_compiled_revision(
    connection: Connection,
    compiled: CompiledConfig,
    *,
    revision_id: str,
    source_name: str | None = None,
    imported_at: datetime | None = None,
    revision_hash: str | None = None,
    engineering_source_hash: str | None = None,
) -> None:
    with connection:
        store_compiled_revision_in_transaction(
            connection,
            compiled,
            revision_id=revision_id,
            source_name=source_name,
            imported_at=imported_at,
            revision_hash=revision_hash,
            engineering_source_hash=engineering_source_hash,
        )


def store_compiled_revision_in_transaction(
    connection: Connection,
    compiled: CompiledConfig,
    *,
    revision_id: str,
    source_name: str | None = None,
    imported_at: datetime | None = None,
    revision_hash: str | None = None,
    engineering_source_hash: str | None = None,
) -> None:
    timestamp = (imported_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    identity_hash = revision_hash or compiled.source_hash

    connection.execute(
        """
        INSERT INTO config_revision(
            revision_id,
            schema_version,
            imported_at_utc,
            revision_hash,
            compiled_hash,
            engineering_source_hash,
            source_name,
            active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            revision_id,
            compiled.schema_version,
            timestamp,
            identity_hash,
            compiled.source_hash,
            engineering_source_hash,
            source_name,
        ),
    )

    connection.executemany(
        """
        INSERT INTO tag_config(
            revision_id, tag_id, entity_id, value_type, stale_after_s, enabled, config_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                revision_id,
                tag.tag_id,
                tag.entity_id,
                tag.value_type,
                tag.stale_after_s,
                int(tag.enabled),
                _json_dump(asdict(tag)),
            )
            for tag in compiled.tags
        ],
    )

    connection.executemany(
        """
        INSERT INTO notification_policy_config(
            revision_id,
            policy_id,
            route_key,
            notify_on_active,
            notify_on_return,
            notify_on_ack,
            notify_delay_s,
            notification_channel,
            notification_group,
            critical,
            enabled,
            config_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            _notification_policy_row(revision_id, policy)
            for policy in compiled.notification_policies
        ],
    )

    connection.executemany(
        """
        INSERT INTO alarm_config(
            revision_id,
            alarm_id,
            source_tag_id,
            kind,
            condition_json,
            hysteresis,
            debounce_on_s,
            debounce_off_s,
            on_delay_s,
            off_delay_s,
            priority,
            category,
            config_json,
            alarm_group_id,
            message,
            rtn_ack_required,
            latching,
            inhibit_by_json,
            notification_policy_id,
            enabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [_alarm_row(revision_id, alarm) for alarm in compiled.alarms],
    )


def load_active_compiled_config(
    connection: Connection,
) -> tuple[str, CompiledConfig] | None:
    revision = connection.execute(
        """
        SELECT revision_id, schema_version, compiled_hash
        FROM config_revision
        WHERE active = 1
        """
    ).fetchone()
    if revision is None:
        return None

    revision_id, schema_version, compiled_hash = revision
    tags = tuple(
        _tag_from_json(row[0])
        for row in connection.execute(
            "SELECT config_json FROM tag_config WHERE revision_id = ? ORDER BY tag_id",
            (revision_id,),
        )
    )
    alarms = tuple(
        _alarm_from_json(row[0])
        for row in connection.execute(
            "SELECT config_json FROM alarm_config WHERE revision_id = ? ORDER BY alarm_id",
            (revision_id,),
        )
    )
    notification_policies = tuple(
        _notification_policy_from_json(row[0])
        for row in connection.execute(
            """
            SELECT config_json
            FROM notification_policy_config
            WHERE revision_id = ?
            ORDER BY policy_id
            """,
            (revision_id,),
        )
    )

    return revision_id, CompiledConfig(
        schema_version=str(schema_version),
        source_hash=str(compiled_hash),
        tags=tags,
        alarms=alarms,
        notification_policies=notification_policies,
    )


def _notification_policy_row(
    revision_id: str,
    policy: NotificationPolicyDefinition,
) -> tuple[Any, ...]:
    return (
        revision_id,
        policy.policy_id,
        policy.route_key,
        int(policy.notify_on_active),
        int(policy.notify_on_return),
        int(policy.notify_on_ack),
        policy.notify_delay_s,
        policy.notification_channel,
        policy.notification_group,
        int(policy.critical),
        int(policy.enabled),
        _json_dump(asdict(policy)),
    )


def _alarm_row(revision_id: str, alarm: AlarmDefinition) -> tuple[Any, ...]:
    if alarm.kind == AlarmKind.ANALOG:
        condition_payload: dict[str, Any] = {
            "condition": alarm.condition,
            "setpoint": alarm.setpoint,
        }
    elif alarm.kind == AlarmKind.DIGITAL:
        condition_payload = {
            "condition": alarm.condition,
            "alarm_value": alarm.alarm_value,
        }
    else:
        condition_payload = {"condition": alarm.condition}

    return (
        revision_id,
        alarm.alarm_id,
        alarm.source_tag_id,
        alarm.kind.value,
        _json_dump(condition_payload),
        alarm.hysteresis,
        alarm.debounce_on_s,
        alarm.debounce_off_s,
        alarm.on_delay_s,
        alarm.off_delay_s,
        alarm.priority,
        alarm.category,
        _json_dump(asdict(alarm)),
        alarm.alarm_group_id,
        alarm.message,
        int(alarm.rtn_ack_required),
        int(alarm.latching),
        _json_dump(alarm.inhibit_by_alarm_ids),
        alarm.notification_policy_id,
        int(alarm.enabled),
    )


def _tag_from_json(value: str) -> TagDefinition:
    payload = _json_object(value)
    return TagDefinition(**payload)


def _alarm_from_json(value: str) -> AlarmDefinition:
    payload = _json_object(value)
    payload["kind"] = AlarmKind(payload["kind"])
    return AlarmDefinition(**payload)


def _notification_policy_from_json(value: str) -> NotificationPolicyDefinition:
    return NotificationPolicyDefinition(**_json_object(value))


def _json_object(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise TypeError("stored configuration JSON must contain an object")
    return payload


def _json_dump(value: Any) -> str:
    return json.dumps(_json_ready(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
