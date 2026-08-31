from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from itertools import pairwise
from sqlite3 import Connection
from typing import Any

from .repository import DraftConflictError, get_draft, list_objects

_TAG_ID = re.compile(r"^T(\d+)$")
_POLICY_ID = re.compile(r"^N(\d+)$")
_NOTIFY_ENTITY = re.compile(r"^notify\.[a-z0-9_]+$")
_ANALOG_LIMITS = (
    ("HIHI", "HIGH_HIGH", "hihi"),
    ("HI", "HIGH", "hi"),
    ("LO", "LOW", "lo"),
    ("LOLO", "LOW_LOW", "lolo"),
)


def next_alarm_id(connection: Connection, draft_id: str) -> int:
    if get_draft(connection, draft_id) is None:
        raise KeyError(draft_id)
    used = [
        int(match.group(1))
        for item in list_objects(connection, draft_id, object_type="TAG")
        if (match := _TAG_ID.fullmatch(str(item["object_id"]))) is not None
    ]
    return max(used, default=0) + 1


def next_notification_group_id(connection: Connection, draft_id: str) -> int:
    if get_draft(connection, draft_id) is None:
        raise KeyError(draft_id)
    used = [
        int(match.group(1))
        for item in list_objects(connection, draft_id, object_type="NOTIFICATION_POLICY")
        if (match := _POLICY_ID.fullmatch(str(item["object_id"]))) is not None
    ]
    return max(used, default=0) + 1


def load_notification_groups(connection: Connection, draft_id: str) -> list[dict[str, object]]:
    if get_draft(connection, draft_id) is None:
        raise KeyError(draft_id)

    groups: list[dict[str, object]] = []
    for item in list_objects(connection, draft_id, object_type="NOTIFICATION_POLICY"):
        match = _POLICY_ID.fullmatch(str(item["object_id"]))
        payload = item["payload"]
        if match is None or not isinstance(payload, dict):
            continue
        targets = payload.get("target_entity_ids")
        groups.append(
            {
                "group_id": int(match.group(1)),
                "name": str(payload.get("display_name") or f"Group {match.group(1)}"),
                "title": str(payload.get("title") or "Open Alarm"),
                "target_entity_ids": [
                    str(value)
                    for value in targets
                    if isinstance(value, str)
                ] if isinstance(targets, list) else [],
                "notify_delay_s": float(payload.get("notify_delay_s", 0) or 0),
                "enabled": bool(payload.get("enabled", True)),
                "row_order": int(item["row_order"]),
            }
        )
    return groups


def load_alarm_table(connection: Connection, draft_id: str) -> list[dict[str, object]]:
    if get_draft(connection, draft_id) is None:
        raise KeyError(draft_id)

    alarms_by_tag: dict[str, list[dict[str, object]]] = {}
    for item in list_objects(connection, draft_id, object_type="ALARM"):
        payload = item["payload"]
        if not isinstance(payload, dict):
            continue
        alarms_by_tag.setdefault(str(payload.get("source_tag_id", "")), []).append(payload)

    rows: list[dict[str, object]] = []
    for tag in list_objects(connection, draft_id, object_type="TAG"):
        match = _TAG_ID.fullmatch(str(tag["object_id"]))
        payload = tag["payload"]
        if match is None or not isinstance(payload, dict):
            continue
        row_id = int(match.group(1))
        alarms = alarms_by_tag.get(str(tag["object_id"]), [])
        first = alarms[0] if alarms else {}
        kind = str(first.get("kind", "ANALOG"))
        thresholds = {"hihi": None, "hi": None, "lo": None, "lolo": None}
        if kind == "ANALOG":
            field_by_condition = {condition: field for _suffix, condition, field in _ANALOG_LIMITS}
            for alarm in alarms:
                field = field_by_condition.get(str(alarm.get("condition", "")))
                if field is not None:
                    thresholds[field] = alarm.get("setpoint")

        policy_match = _POLICY_ID.fullmatch(str(first.get("notification_policy_id") or ""))
        rows.append(
            {
                "alarm_id": row_id,
                "entity_id": str(payload.get("entity_id", "")),
                "kind": kind,
                "condition": str(first.get("condition", "EQUALS")),
                **thresholds,
                "alarm_value": first.get("alarm_value"),
                "priority": str(first.get("priority", "P2")),
                "category": str(first.get("category", "PROCESS")),
                "hysteresis": float(first.get("hysteresis", 0) or 0),
                "debounce_on_s": float(first.get("debounce_on_s", 0) or 0),
                "debounce_off_s": float(first.get("debounce_off_s", 0) or 0),
                "on_delay_s": float(first.get("on_delay_s", 0) or 0),
                "off_delay_s": float(first.get("off_delay_s", 0) or 0),
                "stale_after_s": payload.get("stale_after_s"),
                "message": str(first.get("message", "")),
                "notification_group_id": None if policy_match is None else int(policy_match.group(1)),
                "enabled": bool(payload.get("enabled", True)),
                "row_order": int(tag["row_order"]),
            }
        )
    return rows


def replace_alarm_table(
    connection: Connection,
    *,
    draft_id: str,
    rows: list[dict[str, Any]],
    groups: list[dict[str, Any]] | None = None,
    notification_locale: str = "en",
    expected_updated_at: str,
    now: datetime | None = None,
) -> str:
    if not expected_updated_at.strip():
        raise ValueError("expected_updated_at is required")
    if notification_locale not in {"en", "fi"}:
        raise ValueError("notification locale must be 'en' or 'fi'")

    normalized_groups = _normalize_groups(groups or [], locale=notification_locale)
    known_policy_ids = {policy_id for policy_id, _policy_json, _row_order in normalized_groups}
    normalized_rows: list[tuple[str, str, list[tuple[str, str]], int]] = []
    seen_ids: set[int] = set()

    for index, row in enumerate(rows):
        row_id = int(row["alarm_id"])
        entity_id = str(row["entity_id"]).strip()
        if row_id < 1:
            raise ValueError("alarm_id must be >= 1")
        if not entity_id:
            raise ValueError(f"entity_id is required for alarm {row_id}")
        if row_id in seen_ids:
            raise ValueError(f"duplicate alarm_id: {row_id}")
        seen_ids.add(row_id)

        group_id = row.get("notification_group_id")
        policy_id = None if group_id is None else f"N{int(group_id)}"
        if policy_id is not None and policy_id not in known_policy_ids:
            raise ValueError(f"notification group {group_id} does not exist for alarm {row_id}")

        tag_id = f"T{row_id}"
        enabled = bool(row.get("enabled", True))
        tag_payload = {
            "tag_id": tag_id,
            "entity_id": entity_id,
            "value_type": "auto",
            "stale_after_s": row.get("stale_after_s"),
            "enabled": enabled,
        }
        kind = str(row.get("kind", "ANALOG"))
        alarm_payloads: list[tuple[str, str]] = []
        if kind == "ANALOG":
            limits = [(field, row.get(field)) for _suffix, _condition, field in _ANALOG_LIMITS]
            configured = [(field, float(value)) for field, value in limits if value is not None]
            if not configured:
                raise ValueError(f"analog alarm {row_id} needs at least one limit")
            _validate_analog_limits(row_id, configured)
            for suffix, condition, field in _ANALOG_LIMITS:
                value = row.get(field)
                if value is None:
                    continue
                alarm_id = f"A{row_id}_{suffix}"
                alarm_payloads.append(
                    (
                        alarm_id,
                        _dump(
                            _alarm_payload(
                                row,
                                alarm_id,
                                tag_id,
                                kind,
                                condition,
                                float(value),
                                policy_id,
                            )
                        ),
                    )
                )
        elif kind == "DIGITAL":
            alarm_value = row.get("alarm_value")
            if alarm_value is None or not str(alarm_value).strip():
                raise ValueError(f"digital alarm {row_id} needs an alarm value")
            alarm_id = f"A{row_id}_DIGITAL"
            alarm_payloads.append(
                (
                    alarm_id,
                    _dump(
                        _alarm_payload(
                            row,
                            alarm_id,
                            tag_id,
                            kind,
                            str(row.get("condition", "EQUALS")),
                            None,
                            policy_id,
                        )
                    ),
                )
            )
        elif kind == "DEVICE":
            alarm_id = f"A{row_id}_DEVICE"
            alarm_payloads.append(
                (
                    alarm_id,
                    _dump(
                        _alarm_payload(
                            row,
                            alarm_id,
                            tag_id,
                            kind,
                            str(row.get("condition", "UNAVAILABLE")),
                            None,
                            policy_id,
                        )
                    ),
                )
            )
        else:
            raise ValueError(f"unsupported alarm kind: {kind}")

        normalized_rows.append(
            (
                tag_id,
                _dump(tag_payload),
                alarm_payloads,
                int(row.get("row_order", index)),
            )
        )

    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    try:
        connection.execute("BEGIN IMMEDIATE")
        draft = connection.execute(
            "SELECT updated_at_utc FROM engineering_draft WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if draft is None:
            raise KeyError(draft_id)
        current_updated_at = str(draft[0])
        if current_updated_at != expected_updated_at:
            raise DraftConflictError(current_updated_at)

        connection.execute(
            """
            DELETE FROM engineering_object
            WHERE draft_id = ? AND object_type IN ('TAG', 'ALARM', 'NOTIFICATION_POLICY')
            """,
            (draft_id,),
        )
        for tag_id, tag_json, alarms, row_order in normalized_rows:
            connection.execute(
                """
                INSERT INTO engineering_object(
                    draft_id, object_type, object_id, payload_json, row_order, updated_at_utc
                ) VALUES (?, 'TAG', ?, ?, ?, ?)
                """,
                (draft_id, tag_id, tag_json, row_order, timestamp),
            )
            connection.executemany(
                """
                INSERT INTO engineering_object(
                    draft_id, object_type, object_id, payload_json, row_order, updated_at_utc
                ) VALUES (?, 'ALARM', ?, ?, ?, ?)
                """,
                [
                    (draft_id, alarm_id, alarm_json, row_order, timestamp)
                    for alarm_id, alarm_json in alarms
                ],
            )
        connection.executemany(
            """
            INSERT INTO engineering_object(
                draft_id, object_type, object_id, payload_json, row_order, updated_at_utc
            ) VALUES (?, 'NOTIFICATION_POLICY', ?, ?, ?, ?)
            """,
            [
                (draft_id, policy_id, policy_json, row_order, timestamp)
                for policy_id, policy_json, row_order in normalized_groups
            ],
        )
        connection.execute(
            "UPDATE engineering_draft SET updated_at_utc = ? WHERE draft_id = ?",
            (timestamp, draft_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return timestamp


def _normalize_groups(
    groups: list[dict[str, Any]],
    *,
    locale: str,
) -> list[tuple[str, str, int]]:
    normalized: list[tuple[str, str, int]] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    for index, group in enumerate(groups):
        group_id = int(group["group_id"])
        name = str(group.get("name", "")).strip()
        title = str(group.get("title", "Open Alarm")).strip()
        if group_id < 1:
            raise ValueError("notification group_id must be >= 1")
        if group_id in seen_ids:
            raise ValueError(f"duplicate notification group_id: {group_id}")
        if not name:
            raise ValueError(f"notification group {group_id} needs a name")
        if not title:
            raise ValueError(f"notification group {group_id} needs a title")
        name_key = name.casefold()
        if name_key in seen_names:
            raise ValueError(f"duplicate notification group name: {name}")
        seen_ids.add(group_id)
        seen_names.add(name_key)

        raw_targets = group.get("target_entity_ids")
        if not isinstance(raw_targets, list):
            raise TypeError(f"notification group {group_id} targets must be a list")
        targets = list(dict.fromkeys(str(value).strip() for value in raw_targets if str(value).strip()))
        if not targets:
            raise ValueError(f"notification group {group_id} needs at least one target")
        invalid_target = next((value for value in targets if _NOTIFY_ENTITY.fullmatch(value) is None), None)
        if invalid_target is not None:
            raise ValueError(
                f"notification group {group_id} target must be a notify.* entity: {invalid_target}"
            )
        delay = float(group.get("notify_delay_s", 0) or 0)
        if delay < 0:
            raise ValueError(f"notification delay for group {group_id} must be >= 0")

        policy_id = f"N{group_id}"
        payload = {
            "policy_id": policy_id,
            "route_key": "notify.send_message",
            "display_name": name,
            "title": title,
            "target_entity_ids": targets,
            "notify_on_active": True,
            "notify_on_return": False,
            "notify_on_ack": False,
            "notify_delay_s": delay,
            "notification_channel": None,
            "notification_group": "open_alarm",
            "critical": False,
            "locale": locale,
            "enabled": bool(group.get("enabled", True)),
        }
        normalized.append((policy_id, _dump(payload), int(group.get("row_order", index))))
    return normalized


def _alarm_payload(
    row: dict[str, Any],
    alarm_id: str,
    tag_id: str,
    kind: str,
    condition: str,
    setpoint: float | None,
    notification_policy_id: str | None,
) -> dict[str, object]:
    return {
        "alarm_id": alarm_id,
        "source_tag_id": tag_id,
        "kind": kind,
        "condition": condition,
        "priority": str(row.get("priority", "P2")),
        "category": str(row.get("category", "PROCESS")),
        "message": str(row.get("message", "")),
        "message_fi": "",
        "setpoint": setpoint,
        "hysteresis": float(row.get("hysteresis", 0) or 0),
        "alarm_value": row.get("alarm_value"),
        "debounce_on_s": float(row.get("debounce_on_s", 0) or 0),
        "debounce_off_s": float(row.get("debounce_off_s", 0) or 0),
        "on_delay_s": float(row.get("on_delay_s", 0) or 0),
        "off_delay_s": float(row.get("off_delay_s", 0) or 0),
        "rtn_ack_required": False,
        "latching": False,
        "inhibit_by_alarm_ids": [],
        "notification_policy_id": notification_policy_id,
        "enabled": bool(row.get("enabled", True)),
    }


def _validate_analog_limits(row_id: int, configured: list[tuple[str, float]]) -> None:
    for (upper_name, upper), (lower_name, lower) in pairwise(configured):
        if upper <= lower:
            raise ValueError(
                f"analog alarm {row_id} limits must descend HIHI > HI > LO > LOLO; "
                f"{upper_name}={upper} is not above {lower_name}={lower}"
            )


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
