from datetime import UTC, datetime
from pathlib import Path

import pytest

from open_alarm.backend.auth.models import UserRole
from open_alarm.backend.auth.repository import (
    resolve_ingress_user,
    set_user_locale,
    set_user_role,
)
from open_alarm.backend.db.database import apply_migrations, connect


def test_first_discovered_user_is_admin_and_later_users_are_viewers(tmp_path: Path) -> None:
    connection = connect(tmp_path / "users.db")
    apply_migrations(connection)
    at = datetime(2026, 8, 30, 12, tzinfo=UTC)

    first = resolve_ingress_user(
        connection,
        user_id="ha-user-1",
        user_name="admin-one",
        display_name="Admin One",
        now=at,
    )
    second = resolve_ingress_user(
        connection,
        user_id="ha-user-2",
        user_name="admin-two",
        display_name="Admin Two",
        now=at,
    )

    assert first.role == UserRole.ADMIN
    assert first.locale == "en"
    assert second.role == UserRole.VIEWER
    audits = connection.execute(
        "SELECT target_user_id, details_json FROM operator_audit WHERE action = 'USER_DISCOVERED'"
    ).fetchall()
    details = {str(row[0]): str(row[1]) for row in audits}
    assert '"initial_role":"ADMIN"' in details["ha-user-1"]
    assert '"initial_role":"VIEWER"' in details["ha-user-2"]
    connection.close()


def test_locale_and_role_are_persistent_and_last_admin_is_protected(tmp_path: Path) -> None:
    connection = connect(tmp_path / "roles.db")
    apply_migrations(connection)
    admin = resolve_ingress_user(
        connection,
        user_id="admin",
        user_name="admin",
        display_name="Admin",
    )
    updated = set_user_locale(connection, user_id=admin.user_id, locale="fi")
    assert updated.locale == "fi"

    with pytest.raises(ValueError, match="last Open Alarm administrator"):
        set_user_role(
            connection,
            actor_user_id=admin.user_id,
            target_user_id=admin.user_id,
            role=UserRole.OPERATOR,
        )
    connection.close()
