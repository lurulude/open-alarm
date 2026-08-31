from __future__ import annotations

import json
import re
from datetime import timedelta
from sqlite3 import Connection
from typing import Any

from ..config.models import NotificationPolicyDefinition
from ..db.notification_outbox import (
    cancel_pending_activation_notifications,
    enqueue_notification_in_transaction,
)
from ..domain.engine import AlarmEvent
from ..domain.models import AlarmEventType
from .constants import ACK_ACTION_PREFIX

_ACTIVE_EVENTS = {AlarmEventType.ACTIVATE, AlarmEventType.REACTIVATE}
_RETURN_EVENTS = {AlarmEventType.RETURN}
_ACK_EVENTS = {AlarmEventType.ACK, AlarmEventType.ACK_RETURN}
_CONTEXT_LIMIT = 3
_TAG_SAFE = re.compile(r"[^A-Za-z0-9_-]+")
_OPEN_ALARMS_URI = "/hassio/ingress/open_alarm"


def route_alarm_event_in_transaction(
    connection: Connection,
    *,
    event_id: int,
    alarm_id: str,
    revision_id: str | None,
    origin: str,
    event: AlarmEvent,
    policy: NotificationPolicyDefinition | None,
    priority: str | None,
    message: str,
    raw_value: Any,
    inhibited: bool,
    shelved: bool,
    suppressed: bool,
    out_of_service: bool,
) -> int | None:
    if policy is None or not policy.enabled:
        return None

    if event.event_type in _RETURN_EVENTS | _ACK_EVENTS:
        cancel_pending_activation_notifications(
            connection,
            alarm_id=alarm_id,
            revision_id=revision_id,
            route_key=policy.route_key,
        )

    if inhibited or shelved or suppressed or out_of_service:
        return None

    if event.event_type in _ACTIVE_EVENTS:
        enabled = policy.notify_on_active
        available_at = event.at + timedelta(seconds=policy.notify_delay_s)
    elif event.event_type in _RETURN_EVENTS:
        enabled = policy.notify_on_return
        available_at = event.at
    elif event.event_type in _ACK_EVENTS:
        enabled = policy.notify_on_ack
        available_at = event.at
    else:
        return None

    if not enabled:
        return None

    operator_message = _alarm_operator_text(
        connection,
        revision_id=revision_id,
        alarm_id=alarm_id,
        fallback=message,
        locale=policy.locale,
    )
    source_unit = _source_unit(connection, alarm_id)
    value_label, formatted_value = _notification_value(
        connection,
        revision_id=revision_id,
        alarm_id=alarm_id,
        raw_value=raw_value,
        unit=source_unit,
        locale=policy.locale,
    )
    related_alarms = (
        _unacknowledged_alarm_context(connection, alarm_id=alarm_id, locale=policy.locale)
        if event.event_type in _ACTIVE_EVENTS
        else ()
    )
    payload = build_notification_payload(
        alarm_id=alarm_id,
        event_id=event_id,
        event_type=event.event_type,
        priority=priority,
        message=operator_message,
        raw_value=raw_value,
        unit=source_unit,
        value_label=value_label,
        formatted_value=formatted_value,
        policy=policy,
        related_alarms=related_alarms,
    )
    return enqueue_notification_in_transaction(
        connection,
        dedupe_key=f"alarm-event:{event_id}:policy:{policy.policy_id}",
        alarm_id=alarm_id,
        revision_id=revision_id,
        origin=origin,
        event_type=event.event_type.value,
        route_key=policy.route_key,
        payload=payload,
        available_at=available_at,
        now=event.at,
    )


def build_notification_payload(
    *,
    alarm_id: str,
    event_type: AlarmEventType,
    priority: str | None,
    message: str,
    raw_value: Any,
    policy: NotificationPolicyDefinition,
    event_id: int | None = None,
    related_alarms: tuple[str, ...] = (),
    unit: str | None = None,
    value_label: str | None = None,
    formatted_value: str | None = None,
) -> dict[str, Any]:
    labels = _notification_labels(policy.locale)
    data: dict[str, Any] = {
        "tag": _notification_tag(alarm_id),
        "alarm_id": alarm_id,
        "event_type": event_type.value,
        "url": _OPEN_ALARMS_URI,
    }
    if event_type in _ACTIVE_EVENTS and event_id is not None:
        data["actions"] = [
            {
                "action": f"{ACK_ACTION_PREFIX}{event_id}",
                "title": labels["ack_action"],
                "authenticationRequired": True,
            },
            {
                "action": "URI",
                "title": labels["open_action"],
                "uri": _OPEN_ALARMS_URI,
            },
        ]
    if policy.notification_channel:
        data["channel"] = policy.notification_channel
    if policy.notification_group and not policy.critical:
        data["group"] = policy.notification_group
    if policy.critical:
        data["ttl"] = 0
        data["priority"] = "high"
        data["push"] = {"interruption-level": "critical"}
        if policy.notification_channel:
            data["importance"] = "high"

    body = message.strip() or labels["alarm"]
    if raw_value is not None:
        label = value_label or labels["value"]
        value = formatted_value or _format_value(raw_value, unit)
        body = f"{body}\n{label}: {value}"
    if related_alarms:
        body = f"{body}\n\n{labels['also_active']}:\n" + "\n".join(
            f"• {line}" for line in related_alarms
        )

    payload: dict[str, Any] = {
        "title": policy.title.strip(),
        "message": body,
        "data": data,
    }
    if policy.target_entity_ids:
        payload["_target_entity_ids"] = list(policy.target_entity_ids)
    return payload


def _unacknowledged_alarm_context(
    connection: Connection,
    *,
    alarm_id: str,
    locale: str,
) -> tuple[str, ...]:
    rows = connection.execute(
        """
        SELECT
          c.priority, c.message, c.config_json, t.entity_id,
          s.raw_value_json, s.source_friendly_name, s.source_unit
        FROM alarm_state s
        LEFT JOIN alarm_config c
          ON c.revision_id = s.revision_id AND c.alarm_id = s.alarm_id
        LEFT JOIN tag_config t
          ON t.revision_id = c.revision_id AND t.tag_id = c.source_tag_id
        WHERE s.origin = 'ENGINEERING'
          AND s.alarm_id <> ?
          AND (
            s.lifecycle IN ('ACTIVE_UNACK', 'RTN_UNACK')
            OR (s.lifecycle = 'PENDING_OFF' AND s.pending_origin = 'ACTIVE_UNACK')
          )
          AND s.suppressed = 0
          AND s.inhibited = 0
          AND s.out_of_service = 0
          AND s.shelved_until_utc IS NULL
        ORDER BY
          CASE COALESCE(c.priority, 'P1')
            WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 WHEN 'P4' THEN 4 ELSE 9
          END,
          COALESCE(s.active_since_utc, s.updated_at_utc) DESC,
          s.alarm_id
        LIMIT ?
        """,
        (alarm_id, _CONTEXT_LIMIT + 1),
    ).fetchall()

    visible = [
        _context_line(
            priority=None if row[0] is None else str(row[0]),
            fallback="" if row[1] is None else str(row[1]),
            config_json=None if row[2] is None else str(row[2]),
            entity_id=None if row[3] is None else str(row[3]),
            raw_value_json=None if row[4] is None else str(row[4]),
            friendly_name=None if row[5] is None else str(row[5]),
            unit=None if row[6] is None else str(row[6]),
            locale=locale,
        )
        for row in rows[:_CONTEXT_LIMIT]
    ]
    if len(rows) > _CONTEXT_LIMIT:
        visible.append(_notification_labels(locale)["more_active"])
    return tuple(visible)


def _context_line(
    *,
    priority: str | None,
    fallback: str,
    config_json: str | None,
    entity_id: str | None,
    raw_value_json: str | None,
    friendly_name: str | None,
    unit: str | None,
    locale: str,
) -> str:
    text = _operator_text_from_config(
        config_json=config_json,
        entity_id=entity_id,
        friendly_name=friendly_name,
        fallback=fallback,
        locale=locale,
    )
    raw_value = None if raw_value_json is None else json.loads(raw_value_json)
    _label, displayed = _format_operator_value(
        raw_value,
        config_json=config_json,
        unit=unit,
        locale=locale,
    )
    value_text = "" if raw_value is None else f" · {displayed}"
    prefix = f"{priority} · " if priority else ""
    return f"{prefix}{text}{value_text}"


def _alarm_operator_text(
    connection: Connection,
    *,
    revision_id: str | None,
    alarm_id: str,
    fallback: str,
    locale: str,
) -> str:
    if revision_id is None:
        return fallback.strip() or _notification_labels(locale)["alarm"]

    row = connection.execute(
        """
        SELECT c.config_json, t.entity_id, s.source_friendly_name
        FROM alarm_config c
        LEFT JOIN tag_config t
          ON t.revision_id = c.revision_id AND t.tag_id = c.source_tag_id
        LEFT JOIN alarm_state s
          ON s.revision_id = c.revision_id AND s.alarm_id = c.alarm_id
        WHERE c.revision_id = ? AND c.alarm_id = ?
        """,
        (revision_id, alarm_id),
    ).fetchone()
    if row is None:
        return fallback.strip() or _notification_labels(locale)["alarm"]
    return _operator_text_from_config(
        config_json=None if row[0] is None else str(row[0]),
        entity_id=None if row[1] is None else str(row[1]),
        friendly_name=None if row[2] is None else str(row[2]),
        fallback=fallback,
        locale=locale,
    )


def _source_unit(connection: Connection, alarm_id: str) -> str | None:
    row = connection.execute(
        "SELECT source_unit FROM alarm_state WHERE alarm_id = ?",
        (alarm_id,),
    ).fetchone()
    return None if row is None or row[0] is None else str(row[0])


def _notification_value(
    connection: Connection,
    *,
    revision_id: str | None,
    alarm_id: str,
    raw_value: Any,
    unit: str | None,
    locale: str,
) -> tuple[str, str]:
    config_json = None
    if revision_id is not None:
        row = connection.execute(
            "SELECT config_json FROM alarm_config WHERE revision_id = ? AND alarm_id = ?",
            (revision_id, alarm_id),
        ).fetchone()
        if row is not None and row[0] is not None:
            config_json = str(row[0])
    return _format_operator_value(
        raw_value,
        config_json=config_json,
        unit=unit,
        locale=locale,
    )


def _format_operator_value(
    value: Any,
    *,
    config_json: str | None,
    unit: str | None,
    locale: str,
) -> tuple[str, str]:
    labels = _notification_labels(locale)
    payload = None if config_json is None else json.loads(config_json)
    if isinstance(payload, dict) and str(payload.get("kind") or "") == "DIGITAL":
        state = _digital_source_state(payload, value)
        return labels["state"], _localized_state(state, locale=locale)
    return labels["value"], _format_value(value, unit)


def _digital_source_state(payload: dict[str, Any], raw_alarm: Any) -> str:
    alarm_value = str(payload.get("alarm_value") or "").strip()
    if not isinstance(raw_alarm, bool) or not alarm_value:
        return str(raw_alarm)

    condition = str(payload.get("condition") or "EQUALS")
    if alarm_value.lower() in {"on", "off"}:
        opposite = "off" if alarm_value.lower() == "on" else "on"
        if condition == "EQUALS":
            return alarm_value if raw_alarm else opposite
        if condition == "NOT_EQUALS":
            return opposite if raw_alarm else alarm_value

    if condition == "EQUALS" and raw_alarm:
        return alarm_value
    if condition == "NOT_EQUALS" and not raw_alarm:
        return alarm_value
    return "alarm" if raw_alarm else "normal"


def _localized_state(value: str, *, locale: str) -> str:
    normalized = value.strip().lower()
    if locale == "fi":
        return {
            "on": "päällä",
            "off": "pois päältä",
            "alarm": "hälytystila",
            "normal": "normaali",
        }.get(normalized, value)
    return {
        "alarm": "alarm state",
        "normal": "normal",
    }.get(normalized, value)


def _operator_text_from_config(
    *,
    config_json: str | None,
    entity_id: str | None,
    friendly_name: str | None,
    fallback: str,
    locale: str,
) -> str:
    payload = None if config_json is None else json.loads(config_json)
    if not isinstance(payload, dict):
        return fallback.strip() or friendly_name or entity_id or _notification_labels(locale)["alarm"]

    localized = payload.get("message_fi") if locale == "fi" else payload.get("message")
    if not isinstance(localized, str) or not localized.strip():
        localized = fallback
    base = localized.strip() if isinstance(localized, str) else ""
    if not base:
        base = friendly_name or entity_id or _notification_labels(locale)["alarm"]

    kind = str(payload.get("kind") or "")
    condition = str(payload.get("condition") or "")
    condition_label = _condition_label(kind, condition, locale=locale)
    return f"{base} · {condition_label}" if condition_label else base


def _format_value(value: Any, unit: str | None) -> str:
    text = str(value)
    return f"{text} {unit}" if unit else text


def _condition_label(kind: str, condition: str, *, locale: str) -> str | None:
    if locale == "fi":
        labels = {
            ("ANALOG", "HIGH_HIGH"): "erittäin korkea",
            ("ANALOG", "HIGH"): "korkea",
            ("ANALOG", "LOW"): "matala",
            ("ANALOG", "LOW_LOW"): "erittäin matala",
            ("DEVICE", "UNAVAILABLE"): "ei käytettävissä",
            ("DEVICE", "UNKNOWN"): "tuntematon",
            ("DEVICE", "MISSING"): "puuttuu",
            ("DEVICE", "STALE"): "vanhentunut tieto",
            ("DEVICE", "BAD_QUALITY"): "huono laatu",
        }
    else:
        labels = {
            ("ANALOG", "HIGH_HIGH"): "high-high",
            ("ANALOG", "HIGH"): "high",
            ("ANALOG", "LOW"): "low",
            ("ANALOG", "LOW_LOW"): "low-low",
            ("DEVICE", "UNAVAILABLE"): "unavailable",
            ("DEVICE", "UNKNOWN"): "unknown",
            ("DEVICE", "MISSING"): "missing",
            ("DEVICE", "STALE"): "stale",
            ("DEVICE", "BAD_QUALITY"): "bad quality",
        }
    return labels.get((kind, condition))


def _notification_labels(locale: str) -> dict[str, str]:
    if locale == "fi":
        return {
            "alarm": "HÄLYTYS",
            "return": "PALAUTUI",
            "ack": "KUITTAUS",
            "ack_return": "PALAUTUMINEN KUITATTU",
            "value": "Arvo",
            "state": "Tila",
            "also_active": "Muut kuittaamattomat",
            "more_active": "+ lisää kuittaamattomia",
            "ack_action": "Kuittaa",
            "open_action": "Avaa hälytykset",
        }
    return {
        "alarm": "ALARM",
        "return": "RETURN",
        "ack": "ACK",
        "ack_return": "ACK RETURN",
        "value": "Value",
        "state": "State",
        "also_active": "Also unacknowledged",
        "more_active": "+ more unacknowledged",
        "ack_action": "Acknowledge",
        "open_action": "Open alarms",
    }


def _notification_tag(alarm_id: str) -> str:
    normalized = _TAG_SAFE.sub("_", f"open_alarm_{alarm_id}").strip("_")
    return normalized[:64] or "open_alarm"
