from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from sqlite3 import Connection
from typing import Any
from uuid import uuid4

from ..db.config_repository import load_active_compiled_config


class DraftConflictError(RuntimeError):
    def __init__(self, current_updated_at: str) -> None:
        super().__init__("engineering draft changed on the server")
        self.current_updated_at = current_updated_at


def _iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat()


def create_draft(
    connection: Connection,
    *,
    name: str,
    created_by: str | None,
    clone_active: bool = True,
    now: datetime | None = None,
) -> str:
    if not name.strip():
        raise ValueError("draft name is required")
    timestamp = _iso(now)
    active = load_active_compiled_config(connection) if clone_active else None
    base_revision_id = None if active is None else active[0]
    draft_id = f"draft-{uuid4()}"

    with connection:
        connection.execute(
            """
            INSERT INTO engineering_draft(
                draft_id, name, base_revision_id, created_by, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (draft_id, name.strip(), base_revision_id, created_by, timestamp, timestamp),
        )
        if active is not None:
            revision_id, _compiled = active
            source_objects = list_revision_source_objects(connection, revision_id)
            if not source_objects:
                raise RuntimeError(
                    f"active revision {revision_id} is missing its engineering source snapshot"
                )
            connection.executemany(
                """
                INSERT INTO engineering_object(
                    draft_id, object_type, object_id, payload_json, row_order, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                _draft_rows_from_source(draft_id, source_objects, timestamp),
            )
    return draft_id


def list_drafts(connection: Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT d.draft_id, d.name, d.base_revision_id, d.created_by,
               d.created_at_utc, d.updated_at_utc, COUNT(o.object_id)
        FROM engineering_draft d
        LEFT JOIN engineering_object o ON o.draft_id = d.draft_id
        GROUP BY d.draft_id
        ORDER BY d.updated_at_utc DESC
        """
    ).fetchall()
    return [
        {
            "draft_id": row[0],
            "name": row[1],
            "base_revision_id": row[2],
            "created_by": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "object_count": int(row[6]),
        }
        for row in rows
    ]


def get_draft(connection: Connection, draft_id: str) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT draft_id, name, base_revision_id, created_by, created_at_utc, updated_at_utc
        FROM engineering_draft WHERE draft_id = ?
        """,
        (draft_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "draft_id": row[0],
        "name": row[1],
        "base_revision_id": row[2],
        "created_by": row[3],
        "created_at": row[4],
        "updated_at": row[5],
    }


def list_objects(
    connection: Connection,
    draft_id: str,
    *,
    object_type: str | None = None,
) -> list[dict[str, object]]:
    if object_type is None:
        rows = connection.execute(
            """
            SELECT object_type, object_id, payload_json, row_order
            FROM engineering_object WHERE draft_id = ?
            ORDER BY object_type, row_order, object_id
            """,
            (draft_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT object_type, object_id, payload_json, row_order
            FROM engineering_object WHERE draft_id = ? AND object_type = ?
            ORDER BY row_order, object_id
            """,
            (draft_id, object_type.upper()),
        ).fetchall()
    return [
        {
            "object_type": str(row[0]),
            "object_id": str(row[1]),
            "payload": json.loads(row[2]),
            "row_order": int(row[3]),
        }
        for row in rows
    ]


def list_revision_source_objects(
    connection: Connection,
    revision_id: str,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT object_type, object_id, payload_json, row_order
        FROM config_source_object
        WHERE revision_id = ?
        ORDER BY object_type, row_order, object_id
        """,
        (revision_id,),
    ).fetchall()
    return [
        {
            "object_type": str(row[0]),
            "object_id": str(row[1]),
            "payload": json.loads(row[2]),
            "row_order": int(row[3]),
        }
        for row in rows
    ]


def store_revision_source_objects_in_transaction(
    connection: Connection,
    revision_id: str,
    objects: list[dict[str, object]],
) -> None:
    connection.executemany(
        """
        INSERT INTO config_source_object(
            revision_id, object_type, object_id, payload_json, row_order
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                revision_id,
                str(item["object_type"]).upper(),
                str(item["object_id"]),
                _dump(item["payload"]),
                int(item["row_order"]),
            )
            for item in objects
        ],
    )


def upsert_object(
    connection: Connection,
    *,
    draft_id: str,
    object_type: str,
    object_id: str,
    payload: dict[str, Any],
    row_order: int = 0,
    now: datetime | None = None,
) -> None:
    if get_draft(connection, draft_id) is None:
        raise KeyError(draft_id)
    timestamp = _iso(now)
    normalized_type = object_type.strip().upper()
    if not normalized_type or not object_id.strip():
        raise ValueError("object_type and object_id are required")
    with connection:
        connection.execute(
            """
            INSERT INTO engineering_object(
                draft_id, object_type, object_id, payload_json, row_order, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(draft_id, object_type, object_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                row_order = excluded.row_order,
                updated_at_utc = excluded.updated_at_utc
            """,
            (draft_id, normalized_type, object_id.strip(), _dump(payload), row_order, timestamp),
        )
        connection.execute(
            "UPDATE engineering_draft SET updated_at_utc = ? WHERE draft_id = ?",
            (timestamp, draft_id),
        )


def _draft_rows_from_source(
    draft_id: str,
    objects: list[dict[str, object]],
    timestamp: str,
) -> list[tuple[object, ...]]:
    return [
        (
            draft_id,
            str(item["object_type"]),
            str(item["object_id"]),
            _dump(item["payload"]),
            int(item["row_order"]),
            timestamp,
        )
        for item in objects
    ]


def _dump(value: Any) -> str:
    return json.dumps(_json_ready(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
