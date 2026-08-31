from pathlib import Path
from sqlite3 import DatabaseError

import pytest

from open_alarm.backend.config.compiler import compile_config
from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    NotificationPolicyDefinition,
    TagDefinition,
)
from open_alarm.backend.db.config_repository import (
    load_active_compiled_config,
    store_compiled_revision,
)
from open_alarm.backend.db.database import apply_migrations, connect


def test_compiled_revision_is_stored_transactionally(tmp_path: Path) -> None:
    connection = connect(tmp_path / "open_alarm.db")
    assert apply_migrations(connection) == [1, 2]
    assert apply_migrations(connection) == []

    policy = NotificationPolicyDefinition(
        policy_id="P1_PHONE",
        route_key="notify.mobile_app_phone",
        notify_on_return=True,
        notify_delay_s=10,
        notification_channel="open_alarm_p1",
        critical=True,
    )
    result = compile_config(
        tags=[TagDefinition("TEMP_01", "sensor.temp_01", stale_after_s=120)],
        alarms=[
            AlarmDefinition(
                alarm_id="TEMP_01.HH",
                source_tag_id="TEMP_01",
                kind=AlarmKind.ANALOG,
                condition="HIGH_HIGH",
                priority="P1",
                category="PROCESS",
                setpoint=95.0,
                hysteresis=2.0,
            ),
            AlarmDefinition(
                alarm_id="TEMP_01.HI",
                source_tag_id="TEMP_01",
                kind=AlarmKind.ANALOG,
                condition="HIGH",
                priority="P2",
                category="PROCESS",
                setpoint=85.0,
                hysteresis=2.0,
                latching=True,
                inhibit_by_alarm_ids=("TEMP_01.HH",),
                notification_policy_id="P1_PHONE",
            ),
        ],
        notification_policies=[policy],
        known_entity_ids={"sensor.temp_01"},
    )
    assert result.compiled is not None

    store_compiled_revision(
        connection,
        result.compiled,
        revision_id="rev-0001",
        source_name="test",
    )

    tag = connection.execute(
        "SELECT tag_id, entity_id, stale_after_s FROM tag_config WHERE revision_id = ?",
        ("rev-0001",),
    ).fetchone()
    alarm = connection.execute(
        """
        SELECT alarm_id, alarm_group_id, rtn_ack_required, latching,
               inhibit_by_json, notification_policy_id, enabled
        FROM alarm_config WHERE revision_id = ? AND alarm_id = 'TEMP_01.HI'
        """,
        ("rev-0001",),
    ).fetchone()
    policy_row = connection.execute(
        """
        SELECT policy_id, route_key, notify_on_return, notify_delay_s,
               notification_channel, critical, enabled
        FROM notification_policy_config
        WHERE revision_id = ? AND policy_id = 'P1_PHONE'
        """,
        ("rev-0001",),
    ).fetchone()
    revision = connection.execute(
        """
        SELECT revision_hash, compiled_hash, engineering_source_hash
        FROM config_revision WHERE revision_id = 'rev-0001'
        """
    ).fetchone()

    assert tag == ("TEMP_01", "sensor.temp_01", 120.0)
    assert alarm == ("TEMP_01.HI", None, 0, 1, '["TEMP_01.HH"]', "P1_PHONE", 1)
    assert policy_row == (
        "P1_PHONE",
        "notify.mobile_app_phone",
        1,
        10.0,
        "open_alarm_p1",
        1,
        1,
    )
    assert revision == (result.compiled.source_hash, result.compiled.source_hash, None)
    connection.close()


def test_active_compiled_revision_round_trips(tmp_path: Path) -> None:
    connection = connect(tmp_path / "active.db")
    apply_migrations(connection)

    policy = NotificationPolicyDefinition(
        policy_id="P1_PHONE",
        route_key="notify.mobile_app_phone",
        notify_on_return=True,
    )
    result = compile_config(
        tags=[TagDefinition("TEMP_01", "sensor.temp_01", stale_after_s=60)],
        alarms=[
            AlarmDefinition(
                alarm_id="TEMP_01.HH",
                source_tag_id="TEMP_01",
                kind=AlarmKind.ANALOG,
                condition="HIGH_HIGH",
                priority="P1",
                category="PROCESS",
                setpoint=95.0,
                hysteresis=2.0,
            ),
            AlarmDefinition(
                alarm_id="TEMP_01.HI",
                source_tag_id="TEMP_01",
                kind=AlarmKind.ANALOG,
                condition="HIGH",
                priority="P1",
                category="PROCESS",
                setpoint=80.0,
                hysteresis=2.0,
                on_delay_s=5.0,
                latching=True,
                inhibit_by_alarm_ids=("TEMP_01.HH",),
                notification_policy_id="P1_PHONE",
            ),
        ],
        notification_policies=[policy],
        known_entity_ids={"sensor.temp_01"},
    )
    assert result.compiled is not None

    store_compiled_revision(connection, result.compiled, revision_id="rev-active")
    assert load_active_compiled_config(connection) is None

    with connection:
        connection.execute(
            "UPDATE config_revision SET active = 1 WHERE revision_id = ?",
            ("rev-active",),
        )

    loaded = load_active_compiled_config(connection)
    assert loaded is not None
    revision_id, compiled = loaded
    assert revision_id == "rev-active"
    assert compiled == result.compiled
    assert compiled.notification_policies == (policy,)
    target = next(alarm for alarm in compiled.alarms if alarm.alarm_id == "TEMP_01.HI")
    assert target.latching is True
    assert target.inhibit_by_alarm_ids == ("TEMP_01.HH",)
    assert target.notification_policy_id == "P1_PHONE"
    connection.close()


def test_failed_migration_rolls_back_entire_version(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_good.sql").write_text(
        "CREATE TABLE example(id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    (migrations / "0002_bad.sql").write_text(
        "ALTER TABLE example ADD COLUMN name TEXT;\nTHIS IS NOT SQL;\n",
        encoding="utf-8",
    )
    connection = connect(tmp_path / "rollback.db")

    with pytest.raises(DatabaseError):
        apply_migrations(connection, migrations)

    columns = [row[1] for row in connection.execute("PRAGMA table_info(example)")]
    applied = [row[0] for row in connection.execute("SELECT version FROM schema_migration")]

    assert columns == ["id"]
    assert applied == [1]
    connection.close()
