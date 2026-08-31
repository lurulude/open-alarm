from datetime import UTC, datetime, timedelta
from pathlib import Path

from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    TagDefinition,
)
from open_alarm.backend.db.alarm_query_repository import list_alarm_history, list_alarm_states
from open_alarm.backend.db.config_repository import store_compiled_revision
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.ha.models import normalize_entity_state
from open_alarm.backend.runtime.dispatcher import AlarmDispatcher


def test_alarm_query_exposes_localization_metadata(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    alarm = AlarmDefinition(
        alarm_id="TEMP_HI",
        source_tag_id="TEMP",
        kind=AlarmKind.ANALOG,
        condition="HIGH",
        priority="P1",
        category="PROCESS",
        message="Temperature high",
        message_fi="Lämpötila korkea",
        setpoint=80.0,
        hysteresis=2.0,
    )
    compiled = CompiledConfig(
        schema_version="1.0.0",
        source_hash="query-localization",
        tags=(TagDefinition("TEMP", "sensor.temp"),),
        alarms=(alarm,),
    )
    connection = connect(tmp_path / "alarms.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")
    dispatcher = AlarmDispatcher(compiled, revision_id="r1", connection=connection)

    state = normalize_entity_state(
        "sensor.temp",
        {
            "state": "90",
            "attributes": {
                "friendly_name": "Kattilan lämpötila",
                "unit_of_measurement": "°C",
            },
        },
        observed_at=now,
        source_timestamp=now,
    )
    dispatcher.process_entity(state, now=now)

    rows = list_alarm_states(connection)
    assert len(rows) == 1
    assert rows[0]["message"] == "Temperature high"
    assert rows[0]["message_fi"] == "Lämpötila korkea"
    assert rows[0]["kind"] == "ANALOG"
    assert rows[0]["condition"] == "HIGH"

    with connection:
        connection.execute(
            """
            INSERT INTO app_user(
                user_id, user_name, display_name, role, locale,
                created_at_utc, updated_at_utc, last_seen_at_utc
            ) VALUES (?, ?, ?, 'OPERATOR', 'fi', ?, ?, ?)
            """,
            ("user-1", "janne", "Janne", now.isoformat(), now.isoformat(), now.isoformat()),
        )
    dispatcher.acknowledge("TEMP_HI", user_id="user-1", now=now + timedelta(seconds=1))

    history = list_alarm_history(connection, alarm_id="TEMP_HI")
    assert history[0]["event_type"] == "ACK"
    assert history[0]["user_display_name"] == "Janne"
    assert history[0]["message_fi"] == "Lämpötila korkea"
    assert history[0]["kind"] == "ANALOG"
    assert history[0]["condition"] == "HIGH"
    assert history[0]["source_entity_id"] == "sensor.temp"
    assert history[0]["source_friendly_name"] == "Kattilan lämpötila"
    assert history[0]["source_unit"] == "°C"
    connection.close()
