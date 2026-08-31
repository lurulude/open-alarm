from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HEADERS = {
    "X-Remote-User-Id": "review-admin",
    "X-Remote-User-Name": "review-admin",
    "X-Remote-User-Display-Name": "Review Admin",
}


def _analog_row(entity_id: str) -> dict[str, object]:
    return {
        "alarm_id": 1,
        "entity_id": entity_id,
        "kind": "ANALOG",
        "condition": "HIGH",
        "hihi": 90.0,
        "hi": 80.0,
        "lo": 20.0,
        "lolo": 10.0,
        "alarm_value": None,
        "priority": "P2",
        "category": "PROCESS",
        "hysteresis": 5.0,
        "debounce_on_s": 0.0,
        "debounce_off_s": 0.0,
        "on_delay_s": 0.0,
        "off_delay_s": 0.0,
        "stale_after_s": None,
        "message": "Temperature alarm",
        "enabled": True,
        "row_order": 0,
    }


def _create_and_save(client: TestClient, entity_id: str) -> str:
    created = client.post(
        "/api/engineering/drafts",
        headers=HEADERS,
        json={"name": "Working configuration", "clone_active": False},
    )
    assert created.status_code == 200
    draft_id = created.json()["draft_id"]

    saved = client.put(
        f"/api/engineering/drafts/{draft_id}/alarm-table",
        headers=HEADERS,
        json={
            "expected_updated_at": created.json()["updated_at"],
            "rows": [_analog_row(entity_id)],
        },
    )
    assert saved.status_code == 200
    return str(draft_id)


def test_review_creates_candidate_without_home_assistant_state_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("OPEN_ALARM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPEN_ALARM_ENFORCE_INGRESS_SOURCE", "0")
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        draft_id = _create_and_save(client, "sensor.test_temperature")

        reviewed = client.post(
            f"/api/engineering/drafts/{draft_id}/review",
            headers=HEADERS,
        )
        assert reviewed.status_code == 200
        body = reviewed.json()
        assert body["ok"] is True
        assert body["revision_id"]
        assert body["source_hash"]
        assert body["issues"] == []
        assert body["preview"]["revision_id"] == body["revision_id"]
        assert body["preview"]["tags"]["added"] == ["T1"]
        assert body["preview"]["alarms"]["added"] == [
            "A1_HI",
            "A1_HIHI",
            "A1_LO",
            "A1_LOLO",
        ]

        repeated = client.post(
            f"/api/engineering/drafts/{draft_id}/review",
            headers=HEADERS,
        )
        assert repeated.status_code == 200
        assert repeated.json()["revision_id"] == body["revision_id"]

        assert client.post(
            f"/api/engineering/drafts/{draft_id}/validate",
            headers=HEADERS,
        ).status_code == 404
        assert client.post(
            f"/api/engineering/drafts/{draft_id}/compile",
            headers=HEADERS,
        ).status_code == 404


def test_review_still_validates_home_assistant_entity_id_syntax(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "invalid-entity"
    monkeypatch.setenv("OPEN_ALARM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPEN_ALARM_ENFORCE_INGRESS_SOURCE", "0")

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        draft_id = _create_and_save(client, "Not a valid entity")
        reviewed = client.post(
            f"/api/engineering/drafts/{draft_id}/review",
            headers=HEADERS,
        )

        assert reviewed.status_code == 200
        body = reviewed.json()
        assert body["ok"] is False
        assert body["revision_id"] is None
        assert body["preview"] is None
        assert any(issue["code"] == "INVALID_ENTITY_ID" for issue in body["issues"])
