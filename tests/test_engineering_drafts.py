from pathlib import Path

import pytest

from open_alarm.backend.config.compiler import compile_config
from open_alarm.backend.config.models import AlarmKind, TagDefinition
from open_alarm.backend.db.config_repository import store_compiled_revision
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.engineering.repository import (
    create_draft,
    list_objects,
    list_revision_source_objects,
    upsert_object,
)
from open_alarm.backend.engineering.service import create_revision_from_draft, preview_revision


def _source_shape(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "object_type": item["object_type"],
            "object_id": item["object_id"],
            "payload": item["payload"],
            "row_order": item["row_order"],
        }
        for item in items
    ]


def test_generic_draft_compiles_to_immutable_revision_and_clones_source(tmp_path: Path) -> None:
    connection = connect(tmp_path / "engineering.db")
    assert apply_migrations(connection) == [1, 2]
    draft_id = create_draft(
        connection,
        name="Boiler alarms",
        created_by="engineer",
        clone_active=False,
    )
    upsert_object(
        connection,
        draft_id=draft_id,
        object_type="TAG",
        object_id="TEMP_01",
        payload={"tag_id": "TEMP_01", "entity_id": "sensor.temp_01", "stale_after_s": 60},
        row_order=10,
    )
    upsert_object(
        connection,
        draft_id=draft_id,
        object_type="NOTIFICATION_POLICY",
        object_id="P1_PHONE",
        payload={
            "policy_id": "P1_PHONE",
            "route_key": "notify.mobile_app_phone",
            "notify_on_active": True,
            "notify_on_return": True,
            "notify_delay_s": 10,
            "notification_channel": "open_alarm_p1",
            "critical": True,
            "locale": "fi",
        },
        row_order=20,
    )
    upsert_object(
        connection,
        draft_id=draft_id,
        object_type="ALARM",
        object_id="TEMP_01.HI",
        payload={
            "alarm_id": "TEMP_01.HI",
            "source_tag_id": "TEMP_01",
            "kind": AlarmKind.ANALOG.value,
            "condition": "HIGH",
            "priority": "P1",
            "category": "PROCESS",
            "setpoint": 80.0,
            "hysteresis": 2.0,
            "on_delay_s": 5.0,
            "latching": True,
            "message": "Temperature high",
            "message_fi": "Lämpötila korkea",
            "notification_policy_id": "P1_PHONE",
        },
        row_order=30,
    )

    revision_id, result = create_revision_from_draft(
        connection,
        draft_id,
        user_id="engineer",
        known_entity_ids={"sensor.temp_01"},
    )

    assert result.ok is True
    assert result.compiled is not None
    assert result.compiled.alarms[0].latching is True
    assert result.compiled.alarms[0].message_fi == "Lämpötila korkea"
    assert result.compiled.alarms[0].notification_policy_id == "P1_PHONE"
    assert result.compiled.notification_policies[0].locale == "fi"
    assert revision_id is not None

    revision = connection.execute(
        """
        SELECT revision_hash, compiled_hash, engineering_source_hash
        FROM config_revision WHERE revision_id = ?
        """,
        (revision_id,),
    ).fetchone()
    assert revision is not None
    assert revision[0] != result.compiled.source_hash
    assert revision[1] == result.compiled.source_hash
    assert revision[2]

    original_source = _source_shape(list_objects(connection, draft_id))
    frozen_source = _source_shape(list_revision_source_objects(connection, revision_id))
    assert frozen_source == original_source

    preview = preview_revision(connection, revision_id)
    assert preview["tags"]["added"] == ["TEMP_01"]
    assert preview["alarms"]["added"] == ["TEMP_01.HI"]
    assert preview["notification_policies"]["added"] == ["P1_PHONE"]

    with connection:
        connection.execute("UPDATE config_revision SET active = 1 WHERE revision_id = ?", (revision_id,))
    clone_id = create_draft(
        connection,
        name="Boiler alarms clone",
        created_by="engineer",
        clone_active=True,
    )
    cloned_source = _source_shape(list_objects(connection, clone_id))
    assert cloned_source == original_source
    connection.close()


def test_distinct_engineering_source_can_share_compiled_hash(tmp_path: Path) -> None:
    connection = connect(tmp_path / "source-identity.db")
    apply_migrations(connection)

    revision_ids: list[str] = []
    compiled_hashes: list[str] = []
    for row_order in (0, 100):
        draft_id = create_draft(
            connection,
            name=f"Source {row_order}",
            created_by="engineer",
            clone_active=False,
        )
        upsert_object(
            connection,
            draft_id=draft_id,
            object_type="TAG",
            object_id="TEMP_01",
            payload={"tag_id": "TEMP_01", "entity_id": "sensor.temp_01"},
            row_order=row_order,
        )
        revision_id, result = create_revision_from_draft(
            connection,
            draft_id,
            user_id="engineer",
            known_entity_ids={"sensor.temp_01"},
        )
        assert revision_id is not None
        assert result.compiled is not None
        revision_ids.append(revision_id)
        compiled_hashes.append(result.compiled.source_hash)

    assert revision_ids[0] != revision_ids[1]
    assert compiled_hashes[0] == compiled_hashes[1]
    hashes = connection.execute(
        """
        SELECT revision_hash, compiled_hash, engineering_source_hash
        FROM config_revision ORDER BY revision_id
        """
    ).fetchall()
    assert len({row[0] for row in hashes}) == 2
    assert len({row[1] for row in hashes}) == 1
    assert len({row[2] for row in hashes}) == 2
    connection.close()


def test_active_revision_without_source_snapshot_is_rejected(tmp_path: Path) -> None:
    connection = connect(tmp_path / "missing-source.db")
    apply_migrations(connection)
    result = compile_config(tags=[TagDefinition("TEMP_01", "sensor.temp_01")], alarms=[])
    assert result.compiled is not None
    store_compiled_revision(connection, result.compiled, revision_id="rev-direct")
    with connection:
        connection.execute("UPDATE config_revision SET active = 1 WHERE revision_id = 'rev-direct'")

    with pytest.raises(RuntimeError, match="missing its engineering source snapshot"):
        create_draft(
            connection,
            name="Should fail",
            created_by="engineer",
            clone_active=True,
        )
    connection.close()


def test_invalid_draft_does_not_create_revision(tmp_path: Path) -> None:
    connection = connect(tmp_path / "invalid.db")
    apply_migrations(connection)
    draft_id = create_draft(connection, name="Invalid", created_by="engineer", clone_active=False)
    upsert_object(
        connection,
        draft_id=draft_id,
        object_type="TAG",
        object_id="BAD",
        payload={"tag_id": "BAD", "entity_id": "not-an-entity"},
    )

    revision_id, result = create_revision_from_draft(connection, draft_id, user_id="engineer")

    assert revision_id is None
    assert result.ok is False
    assert connection.execute("SELECT COUNT(*) FROM config_revision").fetchone()[0] == 0
    connection.close()
