from __future__ import annotations

from sqlite3 import Connection

from ..runtime.system_alarms import SYSTEM_ALARM_DEFINITIONS
from .alarm_query_repository import list_alarm_states

BROWSER_VIEWS = (
    "active",
    "unacknowledged",
    "returned_unacknowledged",
    "shelved",
    "inhibited",
    "suppressed",
    "out_of_service",
)

_OPERATIONAL_SQL = (
    "s.suppressed = 0 AND s.inhibited = 0 AND s.out_of_service = 0 "
    "AND s.shelved_until_utc IS NULL"
)
_UNACKNOWLEDGED_SQL = (
    "(s.lifecycle IN ('ACTIVE_UNACK','RTN_UNACK') "
    "OR (s.lifecycle = 'PENDING_OFF' AND s.pending_origin = 'ACTIVE_UNACK'))"
)


def browse_alarm_states(
    connection: Connection,
    *,
    view: str,
    priority: str | None = None,
    category: str | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list[dict[str, object]]:
    if not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")

    has_filter = any(
        value is not None and value.strip()
        for value in (priority, category, search)
    )
    if not has_filter:
        return list_alarm_states(connection, view=view, limit=limit)

    rows = list_alarm_states(connection, view=view, limit=5000)
    return filter_alarm_rows(
        rows,
        priority=priority,
        category=category,
        search=search,
        limit=limit,
    )


def filter_alarm_rows(
    rows: list[dict[str, object]],
    *,
    priority: str | None = None,
    category: str | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list[dict[str, object]]:
    if not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")

    normalized_priority = None if not priority else priority.strip().upper()
    normalized_category = None if not category else category.strip().upper()
    needle = "" if not search else search.strip().casefold()

    result: list[dict[str, object]] = []
    for row in rows:
        if normalized_priority and str(row.get("priority") or "").upper() != normalized_priority:
            continue
        if normalized_category and str(row.get("category") or "").upper() != normalized_category:
            continue
        if needle and not _matches_search(row, needle):
            continue
        result.append(row)
        if len(result) >= limit:
            break
    return result


def alarm_browser_summary(connection: Connection) -> dict[str, object]:
    counts = alarm_view_counts(connection)
    priorities: dict[str, int] = {}
    categories: dict[str, int] = {}

    rows = connection.execute(
        f"""
        SELECT s.alarm_id, s.origin, c.priority, c.category
        FROM alarm_state s
        LEFT JOIN alarm_config c
          ON c.revision_id = s.revision_id AND c.alarm_id = s.alarm_id
        WHERE s.lifecycle <> 'NORMAL' AND {_OPERATIONAL_SQL}
        """
    ).fetchall()
    for alarm_id, origin, configured_priority, configured_category in rows:
        system = (
            SYSTEM_ALARM_DEFINITIONS.get(str(alarm_id))
            if str(origin) == "SYSTEM"
            else None
        )
        priority = str(system.priority if system is not None else configured_priority or "—")
        category = str(system.category if system is not None else configured_category or "—")
        priorities[priority] = priorities.get(priority, 0) + 1
        categories[category] = categories.get(category, 0) + 1

    return {
        "views": counts,
        "priorities": dict(sorted(priorities.items())),
        "categories": dict(sorted(categories.items())),
    }


def alarm_view_counts(connection: Connection) -> dict[str, int]:
    row = connection.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE
                WHEN s.lifecycle <> 'NORMAL' AND {_OPERATIONAL_SQL} THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE
                WHEN {_UNACKNOWLEDGED_SQL} AND {_OPERATIONAL_SQL} THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE
                WHEN s.lifecycle = 'RTN_UNACK' AND {_OPERATIONAL_SQL} THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN s.shelved_until_utc IS NOT NULL THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN s.inhibited = 1 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN s.suppressed = 1 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN s.out_of_service = 1 THEN 1 ELSE 0 END), 0)
        FROM alarm_state s
        """
    ).fetchone()
    if row is None:
        return {view: 0 for view in BROWSER_VIEWS}
    return {view: int(row[index]) for index, view in enumerate(BROWSER_VIEWS)}


def _matches_search(row: dict[str, object], needle: str) -> bool:
    values = (
        row.get("alarm_id"),
        row.get("source_friendly_name"),
        row.get("source_entity_id"),
        row.get("source_tag_id"),
        row.get("message"),
        row.get("message_fi"),
        row.get("category"),
        row.get("alarm_group_id"),
    )
    return any(needle in str(value).casefold() for value in values if value is not None)
