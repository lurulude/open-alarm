from __future__ import annotations

import json
from datetime import UTC, datetime
from sqlite3 import Connection

from .models import SUPPORTED_USER_LOCALES, AppUser, UserRole


def _timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat()


def _initial_role(connection: Connection) -> UserRole:
    admin_count = connection.execute(
        "SELECT COUNT(*) FROM app_user WHERE role = 'ADMIN'"
    ).fetchone()[0]
    return UserRole.ADMIN if int(admin_count) == 0 else UserRole.VIEWER


def resolve_ingress_user(
    connection: Connection,
    *,
    user_id: str,
    user_name: str | None,
    display_name: str | None,
    now: datetime | None = None,
) -> AppUser:
    timestamp = _timestamp(now)
    row = connection.execute(
        "SELECT user_id, user_name, display_name, role, locale FROM app_user WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    if row is None:
        role = _initial_role(connection)
        with connection:
            connection.execute(
                """
                INSERT INTO app_user(
                    user_id, user_name, display_name, role, locale,
                    created_at_utc, updated_at_utc, last_seen_at_utc
                ) VALUES (?, ?, ?, ?, 'en', ?, ?, ?)
                """,
                (user_id, user_name, display_name, role.value, timestamp, timestamp, timestamp),
            )
            connection.execute(
                """
                INSERT INTO operator_audit(
                    action, actor_user_id, target_user_id, at_utc, details_json
                ) VALUES ('USER_DISCOVERED', ?, ?, ?, ?)
                """,
                (
                    user_id,
                    user_id,
                    timestamp,
                    json.dumps(
                        {"initial_role": role.value},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
        return AppUser(user_id, user_name, display_name, role, "en")

    with connection:
        connection.execute(
            """
            UPDATE app_user
            SET user_name = ?, display_name = ?, updated_at_utc = ?, last_seen_at_utc = ?
            WHERE user_id = ?
            """,
            (user_name, display_name, timestamp, timestamp, user_id),
        )

    return AppUser(
        user_id=str(row[0]),
        user_name=user_name,
        display_name=display_name,
        role=UserRole(str(row[3])),
        locale=str(row[4]),
    )


def set_user_locale(
    connection: Connection,
    *,
    user_id: str,
    locale: str,
    now: datetime | None = None,
) -> AppUser:
    if locale not in SUPPORTED_USER_LOCALES:
        raise ValueError(f"unsupported locale: {locale}")
    timestamp = _timestamp(now)
    with connection:
        connection.execute(
            "UPDATE app_user SET locale = ?, updated_at_utc = ? WHERE user_id = ?",
            (locale, timestamp, user_id),
        )
    user = get_user(connection, user_id)
    if user is None:
        raise KeyError(user_id)
    return user


def set_user_role(
    connection: Connection,
    *,
    actor_user_id: str,
    target_user_id: str,
    role: UserRole,
    now: datetime | None = None,
) -> AppUser:
    current = get_user(connection, target_user_id)
    if current is None:
        raise KeyError(target_user_id)

    if current.role == UserRole.ADMIN and role != UserRole.ADMIN:
        admin_count = connection.execute(
            "SELECT COUNT(*) FROM app_user WHERE role = 'ADMIN'"
        ).fetchone()[0]
        if int(admin_count) <= 1:
            raise ValueError("cannot remove the last Open Alarm administrator")

    timestamp = _timestamp(now)
    with connection:
        connection.execute(
            "UPDATE app_user SET role = ?, updated_at_utc = ? WHERE user_id = ?",
            (role.value, timestamp, target_user_id),
        )
        connection.execute(
            """
            INSERT INTO operator_audit(
                action, actor_user_id, target_user_id, at_utc, details_json
            ) VALUES ('ROLE_CHANGED', ?, ?, ?, ?)
            """,
            (
                actor_user_id,
                target_user_id,
                timestamp,
                json.dumps(
                    {"from": current.role.value, "to": role.value},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )

    updated = get_user(connection, target_user_id)
    if updated is None:
        raise KeyError(target_user_id)
    return updated


def get_user(connection: Connection, user_id: str) -> AppUser | None:
    row = connection.execute(
        "SELECT user_id, user_name, display_name, role, locale FROM app_user WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return AppUser(
        user_id=str(row[0]),
        user_name=None if row[1] is None else str(row[1]),
        display_name=None if row[2] is None else str(row[2]),
        role=UserRole(str(row[3])),
        locale=str(row[4]),
    )


def list_users(connection: Connection) -> tuple[AppUser, ...]:
    rows = connection.execute(
        """
        SELECT user_id, user_name, display_name, role, locale
        FROM app_user
        ORDER BY COALESCE(display_name, user_name, user_id)
        """
    ).fetchall()
    return tuple(
        AppUser(
            user_id=str(row[0]),
            user_name=None if row[1] is None else str(row[1]),
            display_name=None if row[2] is None else str(row[2]),
            role=UserRole(str(row[3])),
            locale=str(row[4]),
        )
        for row in rows
    )
