from pathlib import Path

import open_alarm.backend.db.alarm_browser as alarm_browser_module
from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    TagDefinition,
)
from open_alarm.backend.db.alarm_browser import (
    alarm_browser_summary,
    browse_alarm_states,
    filter_alarm_rows,
)
from open_alarm.backend.db.config_repository import store_compiled_revision
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.runtime.system_alarms import HA_CONNECTION_ALARM_ID


def test_alarm_browser_filters_priority_category_and_search() -> None:
    rows = [
        {
            "alarm_id": "P101.TEMP.HI",
            "source_tag_id": "P101.TEMP",
            "source_entity_id": "sensor.p101_temperature",
            "priority": "P1",
            "category": "PROCESS",
            "message": "Pump temperature high",
            "message_fi": "Pumpun lämpötila korkea",
            "alarm_group_id": "P101.TEMP",
        },
        {
            "alarm_id": "P102.COMMS",
            "source_tag_id": "P102.STATUS",
            "source_entity_id": "binary_sensor.p102_status",
            "priority": "P2",
            "category": "SYSTEM",
            "message": "Pump communication fault",
            "message_fi": "Pumpun yhteysvika",
            "alarm_group_id": "P102",
        },
    ]

    assert [row["alarm_id"] for row in filter_alarm_rows(rows, priority="p1")] == [
        "P101.TEMP.HI"
    ]
    assert [row["alarm_id"] for row in filter_alarm_rows(rows, category="system")] == [
        "P102.COMMS"
    ]
    assert [row["alarm_id"] for row in filter_alarm_rows(rows, search="lämpötila")] == [
        "P101.TEMP.HI"
    ]
    assert [row["alarm_id"] for row in filter_alarm_rows(rows, search="p102.status")] == [
        "P102.COMMS"
    ]
    assert [row["alarm_id"] for row in filter_alarm_rows(rows, search="p101_temperature")] == [
        "P101.TEMP.HI"
    ]


def test_alarm_browser_limit_preserves_existing_priority_order() -> None:
    rows = [
        {"alarm_id": "A", "priority": "P1", "category": "PROCESS"},
        {"alarm_id": "B", "priority": "P1", "category": "PROCESS"},
        {"alarm_id": "C", "priority": "P2", "category": "PROCESS"},
    ]

    assert [row["alarm_id"] for row in filter_alarm_rows(rows, limit=2)] == ["A", "B"]


def test_unfiltered_browser_uses_requested_database_limit(monkeypatch) -> None:
    requested_limits: list[int] = []

    def fake_list_alarm_states(connection, *, view: str, limit: int):
        del connection, view
        requested_limits.append(limit)
        return [{"alarm_id": "A"}]

    monkeypatch.setattr(alarm_browser_module, "list_alarm_states", fake_list_alarm_states)

    rows = browse_alarm_states(object(), view="active", limit=7)

    assert rows == [{"alarm_id": "A"}]
    assert requested_limits == [7]


def test_alarm_browser_summary_uses_runtime_flags_and_system_metadata(tmp_path: Path) -> None:
    connection = connect(tmp_path / "alarm-browser.db")
    apply_migrations(connection)
    compiled = CompiledConfig(
        schema_version="1.0.0",
        source_hash="browser-summary",
        tags=(TagDefinition("TEMP", "sensor.temp"),),
        alarms=(
            AlarmDefinition(
                alarm_id="TEMP.HI",
                source_tag_id="TEMP",
                kind=AlarmKind.ANALOG,
                condition="HIGH",
                priority="P1",
                category="PROCESS",
                setpoint=80,
            ),
            AlarmDefinition(
                alarm_id="TEMP.SHELVED",
                source_tag_id="TEMP",
                kind=AlarmKind.ANALOG,
                condition="HIGH_HIGH",
                priority="P2",
                category="PROCESS",
                setpoint=90,
            ),
            AlarmDefinition(
                alarm_id="TEMP.SUPPRESSED",
                source_tag_id="TEMP",
                kind=AlarmKind.ANALOG,
                condition="LOW",
                priority="P3",
                category="PROCESS",
                setpoint=10,
            ),
        ),
    )
    store_compiled_revision(connection, compiled, revision_id="rev-browser")

    with connection:
        connection.executemany(
            """
            INSERT INTO alarm_state(
                alarm_id,
                revision_id,
                lifecycle,
                condition_abnormal,
                updated_at_utc,
                shelved_until_utc,
                suppressed
            ) VALUES (?, 'rev-browser', ?, 1, '2026-08-30T12:00:00+00:00', ?, ?)
            """,
            (
                ("TEMP.HI", "ACTIVE_UNACK", None, 0),
                ("TEMP.SHELVED", "ACTIVE_ACK", "2026-08-31T12:00:00+00:00", 0),
                ("TEMP.SUPPRESSED", "ACTIVE_UNACK", None, 1),
            ),
        )
        connection.execute(
            """
            INSERT INTO alarm_state(
                alarm_id,
                revision_id,
                origin,
                lifecycle,
                condition_abnormal,
                updated_at_utc
            ) VALUES (?, NULL, 'SYSTEM', 'ACTIVE_UNACK', 1, '2026-08-30T12:00:00+00:00')
            """,
            (HA_CONNECTION_ALARM_ID,),
        )

    active_rows = browse_alarm_states(connection, view="active")
    process_alarm = next(row for row in active_rows if row["alarm_id"] == "TEMP.HI")
    assert process_alarm["source_tag_id"] == "TEMP"
    assert process_alarm["source_entity_id"] == "sensor.temp"

    summary = alarm_browser_summary(connection)

    assert summary["views"] == {
        "active": 2,
        "unacknowledged": 2,
        "returned_unacknowledged": 0,
        "shelved": 1,
        "inhibited": 0,
        "suppressed": 1,
        "out_of_service": 0,
    }
    assert summary["priorities"] == {"P1": 2}
    assert summary["categories"] == {"PROCESS": 1, "SYSTEM": 1}
    connection.close()
