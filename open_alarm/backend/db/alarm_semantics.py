from __future__ import annotations

import json
from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True, slots=True)
class AlarmRuntimeSemantics:
    kind: str
    source_tag_id: str
    condition_json: str
    hysteresis: float | None
    debounce_on_s: float
    debounce_off_s: float
    on_delay_s: float
    off_delay_s: float
    rtn_ack_required: bool
    latching: bool
    inhibit_by_json: str
    enabled: bool


def load_revision_alarm_semantics(
    connection: Connection,
    revision_id: str,
) -> dict[str, AlarmRuntimeSemantics]:
    rows = connection.execute(
        """
        SELECT
            alarm_id,
            kind,
            source_tag_id,
            condition_json,
            hysteresis,
            debounce_on_s,
            debounce_off_s,
            on_delay_s,
            off_delay_s,
            rtn_ack_required,
            latching,
            inhibit_by_json,
            enabled
        FROM alarm_config
        WHERE revision_id = ?
        """,
        (revision_id,),
    ).fetchall()
    return {str(row[0]): _semantics(row[1:]) for row in rows}


def load_alarm_semantics(
    connection: Connection,
    *,
    revision_id: str,
    alarm_id: str,
) -> AlarmRuntimeSemantics | None:
    row = connection.execute(
        """
        SELECT
            kind,
            source_tag_id,
            condition_json,
            hysteresis,
            debounce_on_s,
            debounce_off_s,
            on_delay_s,
            off_delay_s,
            rtn_ack_required,
            latching,
            inhibit_by_json,
            enabled
        FROM alarm_config
        WHERE revision_id = ? AND alarm_id = ?
        """,
        (revision_id, alarm_id),
    ).fetchone()
    return None if row is None else _semantics(row)


def can_migrate_live_alarm(
    previous: AlarmRuntimeSemantics | None,
    target: AlarmRuntimeSemantics,
) -> bool:
    if previous is None:
        return False
    if previous == target:
        return True
    if previous.kind != "ANALOG" or target.kind != "ANALOG":
        return False
    if (
        previous.source_tag_id != target.source_tag_id
        or previous.hysteresis != target.hysteresis
        or previous.debounce_on_s != target.debounce_on_s
        or previous.debounce_off_s != target.debounce_off_s
        or previous.on_delay_s != target.on_delay_s
        or previous.off_delay_s != target.off_delay_s
        or previous.rtn_ack_required != target.rtn_ack_required
        or previous.latching != target.latching
        or previous.inhibit_by_json != target.inhibit_by_json
        or previous.enabled != target.enabled
    ):
        return False

    previous_condition = _condition(previous.condition_json)
    target_condition = _condition(target.condition_json)
    return (
        previous_condition is not None
        and target_condition is not None
        and previous_condition.get("condition") == target_condition.get("condition")
    )


def _condition(value: str) -> dict[str, object] | None:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _semantics(row: tuple[object, ...]) -> AlarmRuntimeSemantics:
    return AlarmRuntimeSemantics(
        kind=str(row[0]),
        source_tag_id=str(row[1]),
        condition_json=str(row[2]),
        hysteresis=None if row[3] is None else float(row[3]),
        debounce_on_s=float(row[4]),
        debounce_off_s=float(row[5]),
        on_delay_s=float(row[6]),
        off_delay_s=float(row[7]),
        rtn_ack_required=bool(row[8]),
        latching=bool(row[9]),
        inhibit_by_json=str(row[10]),
        enabled=bool(row[11]),
    )
