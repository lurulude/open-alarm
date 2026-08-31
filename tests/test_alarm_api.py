from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.runtime.system_alarms import SystemAlarmManager

HEADERS = {
    "X-Remote-User-Id": "ha-admin",
    "X-Remote-User-Name": "admin",
    "X-Remote-User-Display-Name": "Admin User",
}


def _prepare_system_alarm(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = connect(data_dir / "open_alarm.db")
    apply_migrations(connection)
    manager = SystemAlarmManager(connection)
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    manager.set_ha_connected(False, reason="test", now=start)
    manager.tick(now=start + timedelta(seconds=10))
    connection.close()


def test_ingress_identity_alarm_read_ack_and_finnish_preference(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    _prepare_system_alarm(data_dir)
    monkeypatch.setenv("OPEN_ALARM_DATA_DIR", str(data_dir))

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        unauthorized = client.get("/api/session")
        assert unauthorized.status_code == 401

        session = client.get("/api/session", headers=HEADERS)
        assert session.status_code == 200
        assert session.json()["role"] == "ADMIN"

        locale = client.put("/api/session/locale", headers=HEADERS, json={"locale": "fi"})
        assert locale.status_code == 200
        assert locale.json()["locale"] == "fi"

        alarms = client.get("/api/alarm-browser", headers=HEADERS)
        assert alarms.status_code == 200
        assert alarms.json()[0]["alarm_id"] == "SYS_HA_CONNECTION_LOST"
        assert alarms.json()[0]["origin"] == "SYSTEM"

        ack = client.post("/api/alarms/SYS_HA_CONNECTION_LOST/ack", headers=HEADERS)
        assert ack.status_code == 200
        assert ack.json()["lifecycle"] == "ACTIVE_ACK"
        assert ack.json()["events"] == ["ACK"]

        history = client.get(
            "/api/alarms/history",
            headers=HEADERS,
            params={"alarm_id": "SYS_HA_CONNECTION_LOST"},
        )
        assert history.status_code == 200
        assert history.json()[0]["event_type"] == "ACK"
        assert history.json()[0]["user_id"] == "ha-admin"
