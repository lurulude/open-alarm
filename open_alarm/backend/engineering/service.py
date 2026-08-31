from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from sqlite3 import Connection
from typing import Any

from ..config.compiler import compile_config
from ..config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    CompileResult,
    IssueSeverity,
    NotificationPolicyDefinition,
    TagDefinition,
    ValidationIssue,
)
from ..db.config_repository import (
    load_active_compiled_config,
    store_compiled_revision_in_transaction,
)
from .repository import (
    get_draft,
    list_objects,
    store_revision_source_objects_in_transaction,
)


def compile_draft(
    connection: Connection,
    draft_id: str,
    *,
    known_entity_ids: set[str] | None = None,
) -> CompileResult:
    if get_draft(connection, draft_id) is None:
        raise KeyError(draft_id)

    tags: list[TagDefinition] = []
    alarms: list[AlarmDefinition] = []
    notification_policies: list[NotificationPolicyDefinition] = []
    issues: list[ValidationIssue] = []

    for item in list_objects(connection, draft_id):
        object_type = str(item["object_type"])
        object_id = str(item["object_id"])
        payload = item["payload"]
        if not isinstance(payload, dict):
            issues.append(_payload_issue(object_type, object_id, "payload must be an object"))
            continue
        try:
            if object_type == "TAG":
                tags.append(TagDefinition(**payload))
            elif object_type == "ALARM":
                alarm_payload = dict(payload)
                alarm_payload["kind"] = AlarmKind(alarm_payload["kind"])
                alarms.append(AlarmDefinition(**alarm_payload))
            elif object_type == "NOTIFICATION_POLICY":
                notification_policies.append(NotificationPolicyDefinition(**payload))
            else:
                issues.append(
                    _payload_issue(
                        object_type,
                        object_id,
                        f"unsupported engineering object type: {object_type}",
                    )
                )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            issues.append(_payload_issue(object_type, object_id, str(exc)))

    if issues:
        return CompileResult(issues=tuple(issues), compiled=None)

    return compile_config(
        tags=tags,
        alarms=alarms,
        notification_policies=notification_policies,
        known_entity_ids=known_entity_ids,
    )


def create_revision_from_draft(
    connection: Connection,
    draft_id: str,
    *,
    user_id: str | None,
    known_entity_ids: set[str] | None = None,
    now: datetime | None = None,
) -> tuple[str | None, CompileResult]:
    source_objects = list_objects(connection, draft_id)
    result = compile_draft(connection, draft_id, known_entity_ids=known_entity_ids)
    if not result.ok or result.compiled is None:
        return None, result

    engineering_hash = _engineering_source_hash(source_objects)
    revision_hash = _revision_hash(result.compiled.source_hash, engineering_hash)
    existing = connection.execute(
        "SELECT revision_id FROM config_revision WHERE revision_hash = ?",
        (revision_hash,),
    ).fetchone()
    if existing is not None:
        return str(existing[0]), result

    timestamp = now or datetime.now(UTC)
    revision_id = f"rev-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{revision_hash[:8]}"
    draft = get_draft(connection, draft_id)
    source_name = None if draft is None else f"draft:{draft['name']}"

    with connection:
        store_compiled_revision_in_transaction(
            connection,
            result.compiled,
            revision_id=revision_id,
            source_name=source_name,
            imported_at=timestamp,
            revision_hash=revision_hash,
            engineering_source_hash=engineering_hash,
        )
        store_revision_source_objects_in_transaction(
            connection,
            revision_id,
            source_objects,
        )
        connection.execute(
            """
            INSERT INTO engineering_audit(
                revision_id, action, object_type, object_id, user_id, at_utc, details_json
            ) VALUES (?, 'CONFIG_IMPORT', 'ENGINEERING_DRAFT', ?, ?, ?, ?)
            """,
            (
                revision_id,
                draft_id,
                user_id,
                timestamp.astimezone(UTC).isoformat(),
                json.dumps(
                    {
                        "compiled_hash": result.compiled.source_hash,
                        "engineering_source_hash": engineering_hash,
                        "revision_hash": revision_hash,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
    return revision_id, result


def list_revisions(connection: Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT revision_id, schema_version, imported_at_utc, revision_hash, source_name, active
        FROM config_revision ORDER BY imported_at_utc DESC
        """
    ).fetchall()
    return [
        {
            "revision_id": row[0],
            "schema_version": row[1],
            "imported_at": row[2],
            "source_hash": row[3],
            "source_name": row[4],
            "active": bool(row[5]),
        }
        for row in rows
    ]


def preview_revision(connection: Connection, revision_id: str) -> dict[str, object]:
    target = _load_revision(connection, revision_id)
    if target is None:
        raise KeyError(revision_id)
    active = load_active_compiled_config(connection)
    current = None if active is None else active[1]
    return {
        "revision_id": revision_id,
        "base_revision_id": None if active is None else active[0],
        "tags": _diff_objects(() if current is None else current.tags, target.tags, "tag_id"),
        "alarms": _diff_objects(
            () if current is None else current.alarms,
            target.alarms,
            "alarm_id",
        ),
        "notification_policies": _diff_objects(
            () if current is None else current.notification_policies,
            target.notification_policies,
            "policy_id",
        ),
    }


def _load_revision(connection: Connection, revision_id: str) -> CompiledConfig | None:
    revision = connection.execute(
        """
        SELECT schema_version, compiled_hash
        FROM config_revision
        WHERE revision_id = ?
        """,
        (revision_id,),
    ).fetchone()
    if revision is None:
        return None

    tags = tuple(
        TagDefinition(**json.loads(row[0]))
        for row in connection.execute(
            "SELECT config_json FROM tag_config WHERE revision_id = ? ORDER BY tag_id",
            (revision_id,),
        )
    )
    alarms: list[AlarmDefinition] = []
    for row in connection.execute(
        "SELECT config_json FROM alarm_config WHERE revision_id = ? ORDER BY alarm_id",
        (revision_id,),
    ):
        payload = json.loads(row[0])
        payload["kind"] = AlarmKind(payload["kind"])
        alarms.append(AlarmDefinition(**payload))
    notification_policies = tuple(
        NotificationPolicyDefinition(**json.loads(row[0]))
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
    return CompiledConfig(
        schema_version=str(revision[0]),
        source_hash=str(revision[1]),
        tags=tags,
        alarms=tuple(alarms),
        notification_policies=notification_policies,
    )


def _engineering_source_hash(objects: list[dict[str, object]]) -> str:
    canonical = [
        {
            "object_type": str(item["object_type"]),
            "object_id": str(item["object_id"]),
            "payload": item["payload"],
            "row_order": int(item["row_order"]),
        }
        for item in objects
    ]
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _revision_hash(compiled_hash: str, engineering_hash: str) -> str:
    encoded = json.dumps(
        {
            "compiled_hash": compiled_hash,
            "engineering_source_hash": engineering_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _diff_objects(
    before: tuple[Any, ...],
    after: tuple[Any, ...],
    id_field: str,
) -> dict[str, list[str]]:
    old = {getattr(item, id_field): item for item in before}
    new = {getattr(item, id_field): item for item in after}
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(
            key
            for key in old.keys() & new.keys()
            if asdict(old[key]) != asdict(new[key])
        ),
    }


def _payload_issue(object_type: str, object_id: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        severity=IssueSeverity.ERROR,
        code="INVALID_ENGINEERING_OBJECT",
        message=message,
        object_type=object_type.lower(),
        object_id=object_id,
    )
