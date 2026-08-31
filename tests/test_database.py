from pathlib import Path

from open_alarm.backend.db.database import (
    MIGRATIONS_DIR,
    apply_migrations,
    checkpoint_wal,
    connect,
    verify_integrity,
)


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "open_alarm.db"
    connection = connect(db_path)

    migration_files = sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql"))
    assert migration_files == ["0001_initial.sql", "0002_runtime_source_metadata.sql"]
    assert apply_migrations(connection) == [1, 2]
    assert apply_migrations(connection) == []
    assert connection.execute("SELECT version FROM schema_migration").fetchall() == [(1,), (2,)]
    verify_integrity(connection)

    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
    wal_autocheckpoint = connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    assert journal_mode == "wal"
    assert synchronous == 2
    assert wal_autocheckpoint == 1000
    assert foreign_keys == 1
    assert busy_timeout == 5000

    state_columns = {row[1] for row in connection.execute("PRAGMA table_info(alarm_state)")}
    assert "pending_deadline_utc" in state_columns
    assert "pending_origin" in state_columns
    assert "debounce_pending_target" in state_columns
    assert "debounce_pending_deadline_utc" in state_columns
    assert "origin" in state_columns
    assert "latched" in state_columns
    assert "inhibited" in state_columns
    assert "inhibited_by_json" in state_columns
    assert "source_friendly_name" in state_columns
    assert "source_unit" in state_columns

    revision_not_null = {
        row[1]: row[3] for row in connection.execute("PRAGMA table_info(alarm_state)")
    }
    assert revision_not_null["revision_id"] == 0

    event_columns = {row[1] for row in connection.execute("PRAGMA table_info(alarm_event)")}
    assert "origin" in event_columns

    alarm_config_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(alarm_config)")
    }
    assert "latching" in alarm_config_columns
    assert "inhibit_by_json" in alarm_config_columns
    assert "notification_policy_id" in alarm_config_columns

    revision_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(config_revision)")
    }
    assert "revision_hash" in revision_columns
    assert "compiled_hash" in revision_columns
    assert "engineering_source_hash" in revision_columns
    assert "source_hash" not in revision_columns

    for table in (
        "config_revision",
        "tag_config",
        "notification_policy_config",
        "alarm_config",
        "alarm_state",
        "alarm_event",
        "engineering_audit",
        "runtime_event",
        "app_user",
        "operator_audit",
        "engineering_draft",
        "engineering_object",
        "config_source_object",
        "notification_outbox",
    ):
        found = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        assert found == (table,)

    tag_columns = {row[1] for row in connection.execute("PRAGMA table_info(tag_config)")}
    assert "entity_id" in tag_columns
    assert "stale_after_s" in tag_columns

    with connection:
        connection.execute(
            "INSERT INTO runtime_event(event_type, event_at_utc) VALUES ('TEST', '2026-08-30T12:00:00+00:00')"
        )
    checkpoint = checkpoint_wal(connection, truncate=True)
    assert checkpoint[0] == 0
    verify_integrity(connection, full=True)
    connection.close()
