from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.engineering.alarm_table import (
    load_alarm_table,
    load_notification_groups,
    next_alarm_id,
    next_notification_group_id,
    replace_alarm_table,
)
from open_alarm.backend.engineering.repository import (
    DraftConflictError,
    create_draft,
    get_draft,
)

HEADERS = {
    "X-Remote-User-Id": "table-admin",
    "X-Remote-User-Name": "table-admin",
    "X-Remote-User-Display-Name": "Table Admin",
}


def _row(alarm_id: int, entity_id: str) -> dict[str, object]:
    return {
        "alarm_id": alarm_id,
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
        "on_delay_s": 2.0,
        "off_delay_s": 3.0,
        "stale_after_s": None,
        "message": "Temperature alarm",
        "notification_group_id": None,
        "enabled": True,
        "row_order": 0,
    }


def _group(group_id: int = 1) -> dict[str, object]:
    return {
        "group_id": group_id,
        "name": "Operators",
        "title": "Kontti",
        "target_entity_ids": ["notify.jannen_puhelin", "notify.wall_tablet"],
        "notify_delay_s": 15.0,
        "enabled": True,
        "row_order": 0,
    }


def test_alarm_table_expands_one_analog_row_to_four_alarms(tmp_path: Path) -> None:
    connection = connect(tmp_path / "table.db")
    apply_migrations(connection)
    base = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
    draft_id = create_draft(
        connection,
        name="Working configuration",
        created_by="engineer",
        clone_active=False,
        now=base,
    )
    draft = get_draft(connection, draft_id)
    assert draft is not None
    assert next_alarm_id(connection, draft_id) == 1

    updated_at = replace_alarm_table(
        connection,
        draft_id=draft_id,
        rows=[_row(1, "sensor.temperature")],
        expected_updated_at=str(draft["updated_at"]),
        now=base + timedelta(seconds=1),
    )

    assert updated_at == (base + timedelta(seconds=1)).isoformat()
    assert load_alarm_table(connection, draft_id) == [_row(1, "sensor.temperature")]
    assert next_alarm_id(connection, draft_id) == 2
    stored = connection.execute(
        "SELECT object_type, object_id FROM engineering_object WHERE draft_id = ? ORDER BY object_type, object_id",
        (draft_id,),
    ).fetchall()
    assert [tuple(row) for row in stored] == [
        ("ALARM", "A1_HI"),
        ("ALARM", "A1_HIHI"),
        ("ALARM", "A1_LO"),
        ("ALARM", "A1_LOLO"),
        ("TAG", "T1"),
    ]
    connection.close()


def test_alarm_table_uses_named_notification_group_for_grouped_row(tmp_path: Path) -> None:
    connection = connect(tmp_path / "notifications.db")
    apply_migrations(connection)
    draft_id = create_draft(
        connection,
        name="Working configuration",
        created_by="engineer",
        clone_active=False,
    )
    draft = get_draft(connection, draft_id)
    assert draft is not None
    assert next_notification_group_id(connection, draft_id) == 1
    row = _row(1, "sensor.temperature")
    row["notification_group_id"] = 1
    group = _group()

    replace_alarm_table(
        connection,
        draft_id=draft_id,
        rows=[row],
        groups=[group],
        notification_locale="fi",
        expected_updated_at=str(draft["updated_at"]),
    )

    assert load_alarm_table(connection, draft_id) == [row]
    assert load_notification_groups(connection, draft_id) == [group]
    assert next_notification_group_id(connection, draft_id) == 2

    policy_row = connection.execute(
        """
        SELECT payload_json
        FROM engineering_object
        WHERE draft_id = ? AND object_type = 'NOTIFICATION_POLICY' AND object_id = 'N1'
        """,
        (draft_id,),
    ).fetchone()
    assert policy_row is not None
    policy = json.loads(str(policy_row[0]))
    assert policy["route_key"] == "notify.send_message"
    assert policy["display_name"] == "Operators"
    assert policy["title"] == "Kontti"
    assert policy["locale"] == "fi"
    assert policy["target_entity_ids"] == ["notify.jannen_puhelin", "notify.wall_tablet"]
    assert policy["notify_on_active"] is True
    assert policy["notify_on_return"] is False
    assert policy["notify_delay_s"] == 15.0

    alarm_rows = connection.execute(
        """
        SELECT payload_json
        FROM engineering_object
        WHERE draft_id = ? AND object_type = 'ALARM'
        ORDER BY object_id
        """,
        (draft_id,),
    ).fetchall()
    assert len(alarm_rows) == 4
    assert {
        json.loads(str(alarm_row[0]))["notification_policy_id"]
        for alarm_row in alarm_rows
    } == {"N1"}
    connection.close()


def test_alarm_table_rejects_invalid_notification_group_target(tmp_path: Path) -> None:
    connection = connect(tmp_path / "bad-notify.db")
    apply_migrations(connection)
    draft_id = create_draft(
        connection,
        name="Working configuration",
        created_by="engineer",
        clone_active=False,
    )
    draft = get_draft(connection, draft_id)
    assert draft is not None
    group = _group()
    group["target_entity_ids"] = ["sensor.not_a_notifier"]

    with pytest.raises(ValueError, match=r"notify\.\* entity"):
        replace_alarm_table(
            connection,
            draft_id=draft_id,
            rows=[_row(1, "sensor.temperature")],
            groups=[group],
            expected_updated_at=str(draft["updated_at"]),
        )
    connection.close()


def test_alarm_table_rejects_missing_notification_group_reference(tmp_path: Path) -> None:
    connection = connect(tmp_path / "missing-group.db")
    apply_migrations(connection)
    draft_id = create_draft(connection, name="Working configuration", created_by="engineer", clone_active=False)
    draft = get_draft(connection, draft_id)
    assert draft is not None
    row = _row(1, "sensor.temperature")
    row["notification_group_id"] = 7

    with pytest.raises(ValueError, match="notification group 7 does not exist"):
        replace_alarm_table(
            connection,
            draft_id=draft_id,
            rows=[row],
            groups=[],
            expected_updated_at=str(draft["updated_at"]),
        )
    connection.close()


def test_alarm_table_rejects_invalid_analog_limit_order(tmp_path: Path) -> None:
    connection = connect(tmp_path / "limits.db")
    apply_migrations(connection)
    draft_id = create_draft(
        connection,
        name="Working configuration",
        created_by="engineer",
        clone_active=False,
    )
    draft = get_draft(connection, draft_id)
    assert draft is not None
    row = _row(1, "sensor.temperature")
    row["hi"] = 95.0

    with pytest.raises(ValueError, match="HIHI > HI > LO > LOLO"):
        replace_alarm_table(
            connection,
            draft_id=draft_id,
            rows=[row],
            expected_updated_at=str(draft["updated_at"]),
        )
    connection.close()


def test_alarm_table_conflict_keeps_existing_rows(tmp_path: Path) -> None:
    connection = connect(tmp_path / "conflict.db")
    apply_migrations(connection)
    base = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    draft_id = create_draft(
        connection,
        name="Working configuration",
        created_by="engineer",
        clone_active=False,
        now=base,
    )
    initial = get_draft(connection, draft_id)
    assert initial is not None
    first_updated = replace_alarm_table(
        connection,
        draft_id=draft_id,
        rows=[_row(1, "sensor.temperature")],
        expected_updated_at=str(initial["updated_at"]),
        now=base + timedelta(seconds=1),
    )

    with pytest.raises(DraftConflictError) as exc_info:
        replace_alarm_table(
            connection,
            draft_id=draft_id,
            rows=[_row(2, "sensor.other")],
            expected_updated_at=str(initial["updated_at"]),
            now=base + timedelta(seconds=2),
        )

    assert exc_info.value.current_updated_at == first_updated
    assert [row["alarm_id"] for row in load_alarm_table(connection, draft_id)] == [1]
    connection.close()


def test_alarm_table_api_assigns_next_ids_and_reports_conflict(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("OPEN_ALARM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPEN_ALARM_ENFORCE_INGRESS_SOURCE", "0")

    from open_alarm.backend.app import app

    with TestClient(app) as client:
        created = client.post(
            "/api/engineering/drafts",
            headers=HEADERS,
            json={"name": "Working configuration", "clone_active": False},
        )
        assert created.status_code == 200
        draft_id = created.json()["draft_id"]
        expected = created.json()["updated_at"]

        next_id = client.get(f"/api/engineering/drafts/{draft_id}/next-alarm-id", headers=HEADERS)
        assert next_id.status_code == 200
        assert next_id.json() == {"alarm_id": 1}
        next_group = client.get(
            f"/api/engineering/drafts/{draft_id}/next-notification-group-id",
            headers=HEADERS,
        )
        assert next_group.status_code == 200
        assert next_group.json() == {"group_id": 1}

        saved = client.put(
            f"/api/engineering/drafts/{draft_id}/alarm-table",
            headers=HEADERS,
            json={
                "expected_updated_at": expected,
                "rows": [_row(1, "sensor.temperature")],
                "notification_groups": [],
            },
        )
        assert saved.status_code == 200
        assert saved.json()["saved"] == 1
        assert saved.json()["saved_notification_groups"] == 0

        conflict = client.put(
            f"/api/engineering/drafts/{draft_id}/alarm-table",
            headers=HEADERS,
            json={
                "expected_updated_at": expected,
                "rows": [_row(2, "sensor.other")],
                "notification_groups": [],
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["current_updated_at"] == saved.json()["updated_at"]

        rows = client.get(f"/api/engineering/drafts/{draft_id}/alarm-table", headers=HEADERS)
        groups = client.get(f"/api/engineering/drafts/{draft_id}/notification-groups", headers=HEADERS)
        assert rows.status_code == 200
        assert groups.status_code == 200
        assert [row["alarm_id"] for row in rows.json()] == [1]
        assert groups.json() == []
