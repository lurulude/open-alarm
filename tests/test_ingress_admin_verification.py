from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import open_alarm.backend.ha.users as users_module
from open_alarm.backend.auth.repository import resolve_ingress_user
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.ha.client import HomeAssistantConnectionError
from open_alarm.backend.ha.users import HomeAssistantUser


def _headers(user_id: str = "ha-user") -> dict[str, str]:
    return {
        "X-Remote-User-Id": user_id,
        "X-Remote-User-Name": user_id,
        "X-Remote-User-Display-Name": user_id,
    }


def _user(
    *,
    user_id: str = "ha-user",
    admin: bool,
    active: bool = True,
    system_generated: bool = False,
) -> HomeAssistantUser:
    return HomeAssistantUser(
        user_id=user_id,
        username=user_id,
        name=user_id,
        is_owner=admin,
        is_active=active,
        system_generated=system_generated,
        group_ids=frozenset({"system-admin"} if admin else {"system-users"}),
    )


def _configure(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    monkeypatch.setenv("OPEN_ALARM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPEN_ALARM_ENFORCE_INGRESS_SOURCE", "0")


def _user_count(data_dir: Path) -> int:
    connection = connect(data_dir / "open_alarm.db")
    try:
        return int(connection.execute("SELECT COUNT(*) FROM app_user").fetchone()[0])
    finally:
        connection.close()


def test_verified_active_ha_admin_bootstraps_open_alarm_admin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "verified"
    _configure(monkeypatch, data_dir)

    async def fetch_user(self, user_id: str) -> HomeAssistantUser | None:
        del self
        return _user(user_id=user_id, admin=True)

    monkeypatch.setattr(users_module.HomeAssistantUserDirectoryClient, "fetch_user", fetch_user)

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        response = client.get("/api/session", headers=_headers())
        assert response.status_code == 200
        assert response.json()["role"] == "ADMIN"

    assert _user_count(data_dir) == 1


def test_later_verified_ha_admin_defaults_to_open_alarm_viewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "least-privilege"
    _configure(monkeypatch, data_dir)

    async def fetch_user(self, user_id: str) -> HomeAssistantUser | None:
        del self
        return _user(user_id=user_id, admin=True)

    monkeypatch.setattr(users_module.HomeAssistantUserDirectoryClient, "fetch_user", fetch_user)

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        first = client.get("/api/session", headers=_headers("ha-admin-1"))
        second = client.get("/api/session", headers=_headers("ha-admin-2"))
        assert first.status_code == 200
        assert first.json()["role"] == "ADMIN"
        assert second.status_code == 200
        assert second.json()["role"] == "VIEWER"

    assert _user_count(data_dir) == 2


def test_non_admin_or_inactive_user_is_rejected_without_account_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "rejected"
    _configure(monkeypatch, data_dir)

    async def fetch_user(self, user_id: str) -> HomeAssistantUser | None:
        del self
        return _user(user_id=user_id, admin=False)

    monkeypatch.setattr(users_module.HomeAssistantUserDirectoryClient, "fetch_user", fetch_user)

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        response = client.get("/api/session", headers=_headers())
        assert response.status_code == 403
        assert "administrator" in response.json()["detail"]

    assert _user_count(data_dir) == 0


def test_ha_authorization_lookup_failure_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "unavailable"
    _configure(monkeypatch, data_dir)

    async def fetch_user(self, user_id: str) -> HomeAssistantUser | None:
        del self, user_id
        raise HomeAssistantConnectionError("HA unavailable")

    monkeypatch.setattr(users_module.HomeAssistantUserDirectoryClient, "fetch_user", fetch_user)

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        response = client.get("/api/session", headers=_headers())
        assert response.status_code == 503

    assert _user_count(data_dir) == 0


def test_existing_open_alarm_admin_is_blocked_after_ha_admin_demotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "demoted"
    data_dir.mkdir(parents=True)
    connection = connect(data_dir / "open_alarm.db")
    apply_migrations(connection)
    resolve_ingress_user(
        connection,
        user_id="ha-user",
        user_name="ha-user",
        display_name="HA User",
    )
    connection.close()
    _configure(monkeypatch, data_dir)

    async def fetch_user(self, user_id: str) -> HomeAssistantUser | None:
        del self
        return _user(user_id=user_id, admin=False)

    monkeypatch.setattr(users_module.HomeAssistantUserDirectoryClient, "fetch_user", fetch_user)

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        response = client.get("/api/session", headers=_headers())
        assert response.status_code == 403

    connection = connect(data_dir / "open_alarm.db")
    try:
        stored_role = connection.execute(
            "SELECT role FROM app_user WHERE user_id = 'ha-user'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert stored_role == "ADMIN"
