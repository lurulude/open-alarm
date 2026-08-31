from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from open_alarm.backend.app import INGRESS_PROXY_IP, ingress_request_allowed
from open_alarm.backend.db.database import connect


def _headers(user_id: str = "ha-user") -> dict[str, str]:
    return {
        "X-Remote-User-Id": user_id,
        "X-Remote-User-Name": user_id,
        "X-Remote-User-Display-Name": user_id,
    }


def _configure(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    monkeypatch.setenv("OPEN_ALARM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPEN_ALARM_ENFORCE_INGRESS_SOURCE", "0")


def _user_count(data_dir: Path) -> int:
    connection = connect(data_dir / "open_alarm.db")
    try:
        return int(connection.execute("SELECT COUNT(*) FROM app_user").fetchone()[0])
    finally:
        connection.close()


def test_first_ingress_user_bootstraps_open_alarm_admin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "first-user"
    _configure(monkeypatch, data_dir)

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        response = client.get("/api/session", headers=_headers("ha-admin-1"))
        assert response.status_code == 200
        assert response.json()["role"] == "ADMIN"

    assert _user_count(data_dir) == 1


def test_later_ingress_user_defaults_to_open_alarm_viewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "least-privilege"
    _configure(monkeypatch, data_dir)

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        first = client.get("/api/session", headers=_headers("ha-admin-1"))
        second = client.get("/api/session", headers=_headers("ha-admin-2"))
        assert first.status_code == 200
        assert first.json()["role"] == "ADMIN"
        assert second.status_code == 200
        assert second.json()["role"] == "VIEWER"

    assert _user_count(data_dir) == 2


def test_missing_ingress_identity_is_rejected_without_account_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "missing-identity"
    _configure(monkeypatch, data_dir)

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        response = client.get("/api/session")
        assert response.status_code == 401
        assert "Ingress user identity" in response.json()["detail"]

    assert _user_count(data_dir) == 0


def test_ingress_source_gate_prevents_header_spoofing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_ALARM_ENFORCE_INGRESS_SOURCE", "1")

    assert ingress_request_allowed(INGRESS_PROXY_IP, "/api/session") is True
    assert ingress_request_allowed("172.30.32.99", "/api/session") is False
    assert ingress_request_allowed(None, "/api/session") is False
    assert ingress_request_allowed("172.30.32.99", "/healthz") is True
