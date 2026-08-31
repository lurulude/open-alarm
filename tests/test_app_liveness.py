from pathlib import Path

from fastapi.testclient import TestClient


def test_watchdog_liveness_is_available_outside_ingress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("OPEN_ALARM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPEN_ALARM_ENFORCE_INGRESS_SOURCE", "1")

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        liveness = client.get("/healthz")
        assert liveness.status_code == 200
        assert liveness.json() == {"status": "ok"}

        protected = client.get("/api/health")
        assert protected.status_code == 403
        assert protected.json()["detail"].endswith("Home Assistant Ingress only")

    assert (data_dir / "open_alarm.db").is_file()
