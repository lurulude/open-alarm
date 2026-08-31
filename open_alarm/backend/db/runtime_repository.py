from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from sqlite3 import Connection
from typing import Any

from ..config.models import NotificationPolicyDefinition
from ..domain.digital import DigitalQualifierState
from ..domain.engine import AlarmRuntimeState, EngineResult
from ..domain.models import AlarmEventType, AlarmLifecycle
from ..notifications.router import route_alarm_event_in_transaction


@dataclass(frozen=True, slots=True)
class PersistedAlarmRuntime:
    state: AlarmRuntimeState
    digital: DigitalQualifierState | None
    raw_value: Any
    qualified_value: Any
    inhibited_by: tuple[str, ...]
    source_friendly_name: str | None = None
    source_unit: str | None = None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _json_load(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def load_alarm_runtime(
    connection: Connection,
    *,
    revision_id: str | None,
    alarm_id: str,
    digital: bool,
    origin: str = "ENGINEERING",
) -> PersistedAlarmRuntime | None:
    row = connection.execute(
        """
        SELECT
            lifecycle,
            condition_abnormal,
            raw_value_json,
            qualified_value_json,
            pending_started_at_utc,
            pending_deadline_utc,
            pending_origin,
            active_since_utc,
            returned_at_utc,
            latched,
            inhibited_by_json,
            debounce_pending_target,
            debounce_pending_started_at_utc,
            debounce_pending_deadline_utc,
            source_friendly_name,
            source_unit
        FROM alarm_state
        WHERE alarm_id = ? AND revision_id IS ? AND origin = ?
        """,
        (alarm_id, revision_id, origin),
    ).fetchone()
    if row is None:
        return None

    lifecycle = AlarmLifecycle(row[0])
    pending_origin = AlarmLifecycle(row[6]) if row[6] is not None else None
    acked = lifecycle == AlarmLifecycle.ACTIVE_ACK or pending_origin == AlarmLifecycle.ACTIVE_ACK
    runtime_state = AlarmRuntimeState(
        lifecycle=lifecycle,
        condition_abnormal=bool(row[1]),
        acked=acked,
        latched=bool(row[9]),
        active_since=_datetime(row[7]),
        returned_at=_datetime(row[8]),
        pending_started_at=_datetime(row[4]),
        pending_deadline=_datetime(row[5]),
        pending_origin=pending_origin,
    )

    digital_state = None
    if digital:
        qualified_value = _json_load(row[3])
        raw_value = _json_load(row[2])
        digital_state = DigitalQualifierState(
            raw_alarm=bool(raw_value) if isinstance(raw_value, bool) else False,
            qualified_alarm=bool(qualified_value) if isinstance(qualified_value, bool) else False,
            pending_target=None if row[11] is None else bool(row[11]),
            pending_started_at=_datetime(row[12]),
            pending_deadline=_datetime(row[13]),
        )

    inhibited_payload = _json_load(row[10])
    inhibited_by = (
        tuple(str(item) for item in inhibited_payload)
        if isinstance(inhibited_payload, list)
        else ()
    )
    return PersistedAlarmRuntime(
        state=runtime_state,
        digital=digital_state,
        raw_value=_json_load(row[2]),
        qualified_value=_json_load(row[3]),
        inhibited_by=inhibited_by,
        source_friendly_name=None if row[14] is None else str(row[14]),
        source_unit=None if row[15] is None else str(row[15]),
    )


def save_alarm_runtime(
    connection: Connection,
    *,
    revision_id: str | None,
    alarm_id: str,
    result: EngineResult,
    digital: DigitalQualifierState | None = None,
    raw_value: Any = None,
    qualified_value: Any = None,
    inhibited_by: tuple[str, ...] = (),
    user_id: str | None = None,
    message: str = "",
    priority: str | None = None,
    notification_policy: NotificationPolicyDefinition | None = None,
    source_friendly_name: str | None = None,
    source_unit: str | None = None,
    now: datetime | None = None,
    origin: str = "ENGINEERING",
) -> None:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    state = result.state
    existing = connection.execute(
        """
        SELECT ack_user_id, ack_at_utc, shelved_until_utc, suppressed, out_of_service
        FROM alarm_state
        WHERE alarm_id = ?
        """,
        (alarm_id,),
    ).fetchone()
    ack_user_id = existing[0] if existing is not None else None
    ack_at_utc = existing[1] if existing is not None else None
    shelved = existing is not None and existing[2] is not None
    suppressed = existing is not None and bool(existing[3])
    out_of_service = existing is not None and bool(existing[4])

    event_types = {event.event_type for event in result.events}
    if AlarmEventType.ACTIVATE in event_types or AlarmEventType.REACTIVATE in event_types:
        ack_user_id = None
        ack_at_utc = None
    if AlarmEventType.ACK in event_types:
        ack_user_id = user_id
        ack_event = next(event for event in result.events if event.event_type == AlarmEventType.ACK)
        ack_at_utc = _iso(ack_event.at)
    if not state.acked and state.pending_origin != AlarmLifecycle.ACTIVE_ACK:
        ack_user_id = None
        ack_at_utc = None

    pending_transition = None
    if state.lifecycle == AlarmLifecycle.PENDING_ON:
        pending_transition = "ON"
    elif state.lifecycle == AlarmLifecycle.PENDING_OFF:
        pending_transition = "OFF"

    debounce_target = None if digital is None or digital.pending_target is None else int(
        digital.pending_target
    )
    debounce_started = None if digital is None else _iso(digital.pending_started_at)
    debounce_deadline = None if digital is None else _iso(digital.pending_deadline)
    normalized_inhibitors = tuple(sorted(set(inhibited_by)))
    event_details = None
    if source_friendly_name is not None or source_unit is not None:
        event_details = {
            "source_friendly_name": source_friendly_name,
            "source_unit": source_unit,
        }

    with connection:
        connection.execute(
            """
            INSERT INTO alarm_state(
                alarm_id,
                revision_id,
                origin,
                lifecycle,
                condition_abnormal,
                raw_value_json,
                qualified_value_json,
                pending_transition,
                pending_started_at_utc,
                pending_deadline_utc,
                pending_origin,
                active_since_utc,
                ack_user_id,
                ack_at_utc,
                returned_at_utc,
                latched,
                inhibited,
                inhibited_by_json,
                updated_at_utc,
                debounce_pending_target,
                debounce_pending_started_at_utc,
                debounce_pending_deadline_utc,
                source_friendly_name,
                source_unit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(alarm_id) DO UPDATE SET
                revision_id = excluded.revision_id,
                origin = excluded.origin,
                lifecycle = excluded.lifecycle,
                condition_abnormal = excluded.condition_abnormal,
                raw_value_json = excluded.raw_value_json,
                qualified_value_json = excluded.qualified_value_json,
                pending_transition = excluded.pending_transition,
                pending_started_at_utc = excluded.pending_started_at_utc,
                pending_deadline_utc = excluded.pending_deadline_utc,
                pending_origin = excluded.pending_origin,
                active_since_utc = excluded.active_since_utc,
                ack_user_id = excluded.ack_user_id,
                ack_at_utc = excluded.ack_at_utc,
                returned_at_utc = excluded.returned_at_utc,
                latched = excluded.latched,
                inhibited = excluded.inhibited,
                inhibited_by_json = excluded.inhibited_by_json,
                updated_at_utc = excluded.updated_at_utc,
                debounce_pending_target = excluded.debounce_pending_target,
                debounce_pending_started_at_utc = excluded.debounce_pending_started_at_utc,
                debounce_pending_deadline_utc = excluded.debounce_pending_deadline_utc,
                source_friendly_name = excluded.source_friendly_name,
                source_unit = excluded.source_unit
            """,
            (
                alarm_id,
                revision_id,
                origin,
                state.lifecycle.value,
                int(state.condition_abnormal),
                _json_dump(raw_value),
                _json_dump(qualified_value),
                pending_transition,
                _iso(state.pending_started_at),
                _iso(state.pending_deadline),
                None if state.pending_origin is None else state.pending_origin.value,
                _iso(state.active_since),
                ack_user_id,
                ack_at_utc,
                _iso(state.returned_at),
                int(state.latched),
                int(bool(normalized_inhibitors)),
                _json_dump(normalized_inhibitors) if normalized_inhibitors else None,
                _iso(timestamp),
                debounce_target,
                debounce_started,
                debounce_deadline,
                source_friendly_name,
                source_unit,
            ),
        )

        for event in result.events:
            cursor = connection.execute(
                """
                INSERT INTO alarm_event(
                    alarm_id,
                    revision_id,
                    origin,
                    event_type,
                    event_at_utc,
                    user_id,
                    value_json,
                    message,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alarm_id,
                    revision_id,
                    origin,
                    event.event_type.value,
                    _iso(event.at),
                    user_id,
                    _json_dump(raw_value),
                    message,
                    _json_dump(event_details),
                ),
            )
            event_id = cursor.lastrowid
            if event_id is None:
                raise RuntimeError("alarm event row was not persisted")
            route_alarm_event_in_transaction(
                connection,
                event_id=int(event_id),
                alarm_id=alarm_id,
                revision_id=revision_id,
                origin=origin,
                event=event,
                policy=notification_policy,
                priority=priority,
                message=message,
                raw_value=raw_value,
                inhibited=bool(normalized_inhibitors),
                shelved=shelved,
                suppressed=suppressed,
                out_of_service=out_of_service,
            )
