from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from sqlite3 import Connection

from .alarm_semantics import (
    can_migrate_live_alarm,
    load_alarm_semantics,
    load_revision_alarm_semantics,
)


class RevisionActivationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ActivationResult:
    active_revision_id: str
    previous_revision_id: str | None
    migrated_alarm_ids: tuple[str, ...]
    reset_alarm_ids: tuple[str, ...]
    already_active: bool = False


def activate_revision(
    connection: Connection,
    revision_id: str,
    *,
    user_id: str | None = None,
    activated_at: datetime | None = None,
) -> ActivationResult:
    timestamp = (activated_at or datetime.now(UTC)).astimezone(UTC).isoformat()

    with connection:
        target = connection.execute(
            "SELECT revision_id, active FROM config_revision WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        if target is None:
            raise RevisionActivationError(f"configuration revision does not exist: {revision_id}")

        current = connection.execute(
            "SELECT revision_id FROM config_revision WHERE active = 1"
        ).fetchone()
        current_revision_id = None if current is None else str(current[0])

        if bool(target[1]):
            return ActivationResult(
                active_revision_id=revision_id,
                previous_revision_id=current_revision_id,
                migrated_alarm_ids=(),
                reset_alarm_ids=(),
                already_active=True,
            )

        target_alarms = load_revision_alarm_semantics(connection, revision_id)
        state_rows = connection.execute(
            """
            SELECT alarm_id, revision_id
            FROM alarm_state
            WHERE origin = 'ENGINEERING'
            """
        ).fetchall()

        migrated: list[str] = []
        reset: list[str] = []

        for alarm_id_raw, state_revision_raw in state_rows:
            alarm_id = str(alarm_id_raw)
            target_semantics = target_alarms.get(alarm_id)
            if target_semantics is None:
                reset.append(alarm_id)
                continue

            previous_semantics = load_alarm_semantics(
                connection,
                revision_id=str(state_revision_raw),
                alarm_id=alarm_id,
            )
            if can_migrate_live_alarm(previous_semantics, target_semantics):
                migrated.append(alarm_id)
            else:
                reset.append(alarm_id)

        if reset:
            connection.executemany(
                "DELETE FROM alarm_state WHERE alarm_id = ? AND origin = 'ENGINEERING'",
                [(alarm_id,) for alarm_id in sorted(set(reset))],
            )

        if migrated:
            connection.executemany(
                """
                UPDATE alarm_state
                SET revision_id = ?, updated_at_utc = ?
                WHERE alarm_id = ? AND origin = 'ENGINEERING'
                """,
                [
                    (revision_id, timestamp, alarm_id)
                    for alarm_id in sorted(set(migrated))
                ],
            )

        connection.execute("UPDATE config_revision SET active = 0 WHERE active = 1")
        connection.execute(
            "UPDATE config_revision SET active = 1 WHERE revision_id = ?",
            (revision_id,),
        )
        connection.execute(
            """
            INSERT INTO engineering_audit(
                revision_id, action, object_type, object_id, user_id, at_utc, details_json
            ) VALUES (?, 'CONFIG_ACTIVATE', 'CONFIG_REVISION', ?, ?, ?, ?)
            """,
            (
                revision_id,
                revision_id,
                user_id,
                timestamp,
                json.dumps(
                    {
                        "previous_revision_id": current_revision_id,
                        "migrated_alarm_ids": sorted(set(migrated)),
                        "reset_alarm_ids": sorted(set(reset)),
                    },
                    separators=(",", ":"),
                ),
            ),
        )

    return ActivationResult(
        active_revision_id=revision_id,
        previous_revision_id=current_revision_id,
        migrated_alarm_ids=tuple(sorted(set(migrated))),
        reset_alarm_ids=tuple(sorted(set(reset))),
    )
