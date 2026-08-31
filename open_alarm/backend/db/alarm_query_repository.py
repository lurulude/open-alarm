from __future__ import annotations

import json
from sqlite3 import Connection
from typing import Any

from ..runtime.system_alarms import SYSTEM_ALARM_DEFINITIONS


def _json_load(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _message_fi(config_json: str | None) -> str | None:
    payload = _json_load(config_json)
    if not isinstance(payload, dict):
        return None
    value = payload.get("message_fi")
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _config_value(config_json: str | None, key: str) -> str | None:
    payload = _json_load(config_json)
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def list_alarm_states(
    connection: Connection,
    *,
    view: str = "active",
    limit: int = 500,
) -> list[dict[str, object]]:
    if not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")

    operational = (
        "s.suppressed = 0 AND s.inhibited = 0 AND s.out_of_service = 0 "
        "AND s.shelved_until_utc IS NULL"
    )
    clauses: list[str] = []
    if view == "active":
        clauses.extend(["s.lifecycle <> 'NORMAL'", operational])
    elif view == "unacknowledged":
        clauses.extend(
            [
                (
                    "(s.lifecycle IN ('ACTIVE_UNACK','RTN_UNACK') "
                    "OR (s.lifecycle = 'PENDING_OFF' AND s.pending_origin = 'ACTIVE_UNACK'))"
                ),
                operational,
            ]
        )
    elif view == "returned_unacknowledged":
        clauses.extend(["s.lifecycle = 'RTN_UNACK'", operational])
    elif view == "shelved":
        clauses.append("s.shelved_until_utc IS NOT NULL")
    elif view == "suppressed":
        clauses.append("s.suppressed = 1")
    elif view == "inhibited":
        clauses.append("s.inhibited = 1")
    elif view == "out_of_service":
        clauses.append("s.out_of_service = 1")
    elif view == "all":
        pass
    else:
        raise ValueError(f"unsupported alarm view: {view}")

    where = "" if not clauses else "WHERE " + " AND ".join(clauses)
    rows = connection.execute(
        f"""
        SELECT
            s.alarm_id,
            s.revision_id,
            s.origin,
            s.lifecycle,
            s.condition_abnormal,
            s.raw_value_json,
            s.qualified_value_json,
            s.pending_started_at_utc,
            s.pending_deadline_utc,
            s.pending_origin,
            s.active_since_utc,
            s.ack_user_id,
            s.ack_at_utc,
            s.returned_at_utc,
            s.latched,
            s.shelved_until_utc,
            s.suppressed,
            s.inhibited,
            s.inhibited_by_json,
            s.out_of_service,
            s.updated_at_utc,
            c.priority,
            c.category,
            c.message,
            c.config_json,
            c.alarm_group_id,
            c.source_tag_id,
            t.entity_id,
            s.source_friendly_name,
            s.source_unit
        FROM alarm_state s
        LEFT JOIN alarm_config c
          ON c.revision_id = s.revision_id AND c.alarm_id = s.alarm_id
        LEFT JOIN tag_config t
          ON t.revision_id = c.revision_id AND t.tag_id = c.source_tag_id
        {where}
        ORDER BY
            CASE COALESCE(c.priority, 'P1')
                WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 WHEN 'P4' THEN 4 ELSE 9
            END,
            COALESCE(s.active_since_utc, s.updated_at_utc) DESC,
            s.alarm_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    result: list[dict[str, object]] = []
    for row in rows:
        alarm_id = str(row[0])
        system = SYSTEM_ALARM_DEFINITIONS.get(alarm_id) if row[2] == "SYSTEM" else None
        inhibited_by = _json_load(row[18])
        config_json = None if system is not None else row[24]
        result.append(
            {
                "alarm_id": alarm_id,
                "revision_id": row[1],
                "origin": str(row[2]),
                "lifecycle": str(row[3]),
                "condition_abnormal": bool(row[4]),
                "raw_value": _json_load(row[5]),
                "qualified_value": _json_load(row[6]),
                "pending_started_at": row[7],
                "pending_deadline": row[8],
                "pending_origin": row[9],
                "active_since": row[10],
                "ack_user_id": row[11],
                "ack_at": row[12],
                "returned_at": row[13],
                "latched": bool(row[14]),
                "shelved_until": row[15],
                "suppressed": bool(row[16]),
                "inhibited": bool(row[17]),
                "inhibited_by": inhibited_by if isinstance(inhibited_by, list) else [],
                "out_of_service": bool(row[19]),
                "updated_at": row[20],
                "priority": system.priority if system is not None else row[21],
                "category": system.category if system is not None else row[22],
                "message": system.message if system is not None else row[23],
                "message_fi": None if system is not None else _message_fi(config_json),
                "message_key": None if system is None else system.message_key,
                "kind": None if system is not None else _config_value(config_json, "kind"),
                "condition": None if system is not None else _config_value(config_json, "condition"),
                "alarm_group_id": row[25],
                "source_tag_id": None if system is not None else row[26],
                "source_entity_id": None if system is not None else row[27],
                "source_friendly_name": None if system is not None else row[28],
                "source_unit": None if system is not None else row[29],
            }
        )
    return result


def list_alarm_history(
    connection: Connection,
    *,
    alarm_id: str | None = None,
    before_event_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, object]]:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")

    clauses: list[str] = []
    parameters: list[object] = []
    if alarm_id is not None:
        clauses.append("e.alarm_id = ?")
        parameters.append(alarm_id)
    if before_event_id is not None:
        clauses.append("e.event_id < ?")
        parameters.append(before_event_id)
    where = "" if not clauses else "WHERE " + " AND ".join(clauses)
    parameters.append(limit)

    rows = connection.execute(
        f"""
        SELECT
            e.event_id,
            e.alarm_id,
            e.revision_id,
            e.origin,
            e.event_type,
            e.event_at_utc,
            e.user_id,
            e.value_json,
            e.message,
            e.details_json,
            c.config_json,
            t.entity_id,
            s.source_friendly_name,
            s.source_unit,
            u.display_name,
            u.user_name
        FROM alarm_event e
        LEFT JOIN alarm_config c
          ON c.revision_id = e.revision_id AND c.alarm_id = e.alarm_id
        LEFT JOIN tag_config t
          ON t.revision_id = c.revision_id AND t.tag_id = c.source_tag_id
        LEFT JOIN alarm_state s
          ON s.alarm_id = e.alarm_id
        LEFT JOIN app_user u
          ON u.user_id = e.user_id
        {where}
        ORDER BY e.event_id DESC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        alarm_id_value = str(row[1])
        origin = str(row[3])
        system = SYSTEM_ALARM_DEFINITIONS.get(alarm_id_value) if origin == "SYSTEM" else None
        details = _json_load(row[9])
        metadata = details if isinstance(details, dict) else {}
        config_json = None if system is not None else row[10]
        source_friendly_name = metadata.get("source_friendly_name") or row[12]
        source_unit = metadata.get("source_unit") or row[13]
        user_display_name = row[14] or row[15]
        result.append(
            {
                "event_id": int(row[0]),
                "alarm_id": alarm_id_value,
                "revision_id": row[2],
                "origin": origin,
                "event_type": str(row[4]),
                "event_at": str(row[5]),
                "user_id": row[6],
                "user_display_name": user_display_name,
                "value": _json_load(row[7]),
                "message": system.message if system is not None else row[8],
                "message_fi": None if system is not None else _message_fi(config_json),
                "message_key": None if system is None else system.message_key,
                "kind": None if system is not None else _config_value(config_json, "kind"),
                "condition": None if system is not None else _config_value(config_json, "condition"),
                "details": details,
                "source_entity_id": None if system is not None else row[11],
                "source_friendly_name": None if system is not None else source_friendly_name,
                "source_unit": None if system is not None else source_unit,
            }
        )
    return result
