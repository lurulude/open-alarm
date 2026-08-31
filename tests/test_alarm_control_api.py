from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    TagDefinition,
)
from open_alarm.backend.db.config_repository import store_compiled_revision
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.ha.models import normalize_entity_state
from open_alarm.backend.runtime.dispatcher import AlarmDispatcher
from open_alarm.backend.runtime.system_alarms import SystemAlarmManager

HEADERS = {
    "X-Remote-User-Id": "ha-admin-controls",
    "X-Remote-User-Name": "admin",
    "X-Remote-User-Display-Name": "Admin Controls",
}
ENGINEERING_ALARM_ID = "TEMP_HI"
SYSTEM_ALARM_ID = "SYS_HA_CONNECTION_LOST"


def _state(value: str, at: datetime):
    return normalize_entity_state(
        "sensor.temp",
        {"state": value, "attributes": {}},
        observed_at=at,
        source_timestamp=at,
    )


def _prepare(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = connect(data_dir / "open_alarm.db")
    apply_migrations(connection)

    compiled = CompiledConfig(
        schema_version="1.0.0",
        source_hash="control-api",
        tags=(TagDefinition("TEMP", "sensor.temp"),),
        alarms=(
            AlarmDefinition(
                alarm_id=ENGINEERING_ALARM_ID,
                source_tag_id="TEMP",
                kind=AlarmKind.ANALOG,
                condition="HIGH",
                priority="P1",
                category="PROCESS",
                setpoint=80.0,
                hysteresis=2.0,
            ),
        ),
    )
    store_compiled_revision(connection, compiled, revision_id="control-api-r1")
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    dispatcher = AlarmDispatcher(compiled, revision_id="control-api-r1", connection=connection)
    dispatcher.process_entity(_state("90", start), now=start)

    manager = SystemAlarmManager(connection)
    manager.set_ha_connected(False, reason="test", now=start)
    manager.tick(now=start + timedelta(seconds=10))
    connection.close()


def test_alarm_control_endpoints_and_dedicated_views(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    _prepare(data_dir)
    monkeypatch.setenv("OPEN_ALARM_DATA_DIR", str(data_dir))

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        shelf = client.post(
            f"/api/alarms/{ENGINEERING_ALARM_ID}/shelve",
            headers=HEADERS,
            json={"duration_s": 3600, "reason": "operator test"},
        )
        assert shelf.status_code == 200
        assert shelf.json()["shelved"] is True
        assert client.get("/api/alarm-browser?view=shelved", headers=HEADERS).json()[0][
            "alarm_id"
        ] == ENGINEERING_ALARM_ID

        unshelve = client.post(
            f"/api/alarms/{ENGINEERING_ALARM_ID}/unshelve",
            headers=HEADERS,
        )
        assert unshelve.status_code == 200
        assert unshelve.json()["changed"] is True

        suppress = client.post(
            f"/api/alarms/{ENGINEERING_ALARM_ID}/suppress",
            headers=HEADERS,
            json={"reason": ""},
        )
        assert suppress.status_code == 200
        assert suppress.json()["changed"] is True
        assert client.get("/api/alarm-browser?view=suppressed", headers=HEADERS).json()[0][
            "alarm_id"
        ] == ENGINEERING_ALARM_ID

        unsuppress = client.post(
            f"/api/alarms/{ENGINEERING_ALARM_ID}/unsuppress",
            headers=HEADERS,
            json={"reason": None},
        )
        assert unsuppress.status_code == 200

        oos = client.post(
            f"/api/alarms/{ENGINEERING_ALARM_ID}/out-of-service",
            headers=HEADERS,
            json={"reason": ""},
        )
        assert oos.status_code == 200
        assert oos.json()["changed"] is True
        assert client.get("/api/alarm-browser?view=out_of_service", headers=HEADERS).json()[0][
            "alarm_id"
        ] == ENGINEERING_ALARM_ID

        in_service = client.post(
            f"/api/alarms/{ENGINEERING_ALARM_ID}/in-service",
            headers=HEADERS,
            json={"reason": None},
        )
        assert in_service.status_code == 200


def test_system_alarm_hide_controls_return_conflict(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data-system"
    _prepare(data_dir)
    monkeypatch.setenv("OPEN_ALARM_DATA_DIR", str(data_dir))

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        shelf = client.post(
            f"/api/alarms/{SYSTEM_ALARM_ID}/shelve",
            headers=HEADERS,
            json={"duration_s": 3600, "reason": "not allowed"},
        )
        assert shelf.status_code == 409
        assert "system alarms cannot be shelved" in shelf.json()["detail"]

        suppress = client.post(
            f"/api/alarms/{SYSTEM_ALARM_ID}/suppress",
            headers=HEADERS,
            json={"reason": "not allowed"},
        )
        assert suppress.status_code == 409
        assert "system alarms cannot be suppressed" in suppress.json()["detail"]

        oos = client.post(
            f"/api/alarms/{SYSTEM_ALARM_ID}/out-of-service",
            headers=HEADERS,
            json={"reason": "not allowed"},
        )
        assert oos.status_code == 409
        assert "system alarms cannot be taken out of service" in oos.json()["detail"]

        active = client.get("/api/alarm-browser?view=active", headers=HEADERS).json()
        assert SYSTEM_ALARM_ID in {row["alarm_id"] for row in active}
