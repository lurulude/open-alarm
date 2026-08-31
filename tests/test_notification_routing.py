import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    NotificationPolicyDefinition,
    TagDefinition,
)
from open_alarm.backend.db.alarm_control_repository import set_suppressed
from open_alarm.backend.db.alarm_query_repository import list_alarm_history, list_alarm_states
from open_alarm.backend.db.config_repository import store_compiled_revision
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.ha.models import normalize_entity_state
from open_alarm.backend.runtime.dispatcher import AlarmDispatcher


def _state(value: str, at: datetime):
    return normalize_entity_state(
        "binary_sensor.fault",
        {"state": value, "attributes": {}},
        observed_at=at,
        source_timestamp=at,
    )


def _temperature_state(value: str, at: datetime):
    return normalize_entity_state(
        "sensor.temperature",
        {
            "state": value,
            "attributes": {
                "friendly_name": "Poreamme esilämmitys halvoilla tunneilla",
                "unit_of_measurement": "°C",
            },
        },
        observed_at=at,
        source_timestamp=at,
    )


def _compiled(
    *alarms: AlarmDefinition,
    policy: NotificationPolicyDefinition,
) -> CompiledConfig:
    return CompiledConfig(
        schema_version="1.0.0",
        source_hash="notification-routing-test",
        tags=(TagDefinition("FAULT", "binary_sensor.fault"),),
        alarms=alarms,
        notification_policies=(policy,),
    )


def _alarm(alarm_id: str, *, inhibit_by: tuple[str, ...] = ()) -> AlarmDefinition:
    return AlarmDefinition(
        alarm_id=alarm_id,
        source_tag_id="FAULT",
        kind=AlarmKind.DIGITAL,
        condition="EQUALS",
        alarm_value="on",
        priority="P1",
        category="PROCESS",
        message=f"{alarm_id} active",
        message_fi=f"{alarm_id} hälytys",
        inhibit_by_alarm_ids=inhibit_by,
        notification_policy_id="P1_PHONE",
    )


def test_activation_event_and_notification_intent_commit_together(tmp_path: Path) -> None:
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    policy = NotificationPolicyDefinition(
        policy_id="P1_PHONE",
        route_key="notify.mobile_app_phone",
        notification_channel="open_alarm_p1",
        critical=True,
    )
    compiled = _compiled(_alarm("FAULT_1"), policy=policy)
    connection = connect(tmp_path / "routing.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")
    dispatcher = AlarmDispatcher(compiled, revision_id="r1", connection=connection)

    dispatcher.process_entity(_state("on", start), now=start)

    event = connection.execute(
        "SELECT event_id, event_type FROM alarm_event WHERE alarm_id = 'FAULT_1'"
    ).fetchone()
    outbox = connection.execute(
        """
        SELECT dedupe_key, event_type, route_key, status, available_at_utc
        FROM notification_outbox WHERE alarm_id = 'FAULT_1'
        """
    ).fetchone()
    assert event is not None
    assert event[1] == "ACTIVATE"
    assert outbox == (
        f"alarm-event:{event[0]}:policy:P1_PHONE",
        "ACTIVATE",
        "notify.mobile_app_phone",
        "PENDING",
        start.isoformat(),
    )
    connection.close()


def test_finnish_policy_uses_finnish_alarm_text_and_configured_title(tmp_path: Path) -> None:
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    policy = NotificationPolicyDefinition(
        policy_id="P1_PHONE",
        route_key="notify.mobile_app_phone",
        locale="fi",
    )
    compiled = _compiled(_alarm("FAULT_1"), policy=policy)
    connection = connect(tmp_path / "routing-fi.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")
    dispatcher = AlarmDispatcher(compiled, revision_id="r1", connection=connection)

    dispatcher.process_entity(_state("on", start), now=start)

    row = connection.execute(
        "SELECT payload_json FROM notification_outbox WHERE alarm_id = 'FAULT_1'"
    ).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    assert payload["title"] == "Open Alarm"
    assert payload["message"] == "FAULT_1 hälytys\nTila: päällä"
    connection.close()


def test_analog_notification_uses_friendly_name_unit_and_existing_alarm(tmp_path: Path) -> None:
    start = datetime(2026, 8, 31, 9, tzinfo=UTC)
    policy = NotificationPolicyDefinition(
        policy_id="N1",
        route_key="notify.send_message",
        title="Kontti",
        target_entity_ids=("notify.jannen_puhelin",),
        locale="fi",
    )
    low_alarm = AlarmDefinition(
        alarm_id="A2_LO",
        source_tag_id="TEMP",
        kind=AlarmKind.ANALOG,
        condition="LOW",
        setpoint=20,
        priority="P2",
        category="PROCESS",
        message="",
        notification_policy_id="N1",
    )
    low_low_alarm = AlarmDefinition(
        alarm_id="A2_LOLO",
        source_tag_id="TEMP",
        kind=AlarmKind.ANALOG,
        condition="LOW_LOW",
        setpoint=10,
        priority="P2",
        category="PROCESS",
        message="",
        notification_policy_id="N1",
    )
    compiled = CompiledConfig(
        schema_version="1.0.0",
        source_hash="notification-operator-text-test",
        tags=(TagDefinition("TEMP", "sensor.temperature"),),
        alarms=(low_alarm, low_low_alarm),
        notification_policies=(policy,),
    )
    connection = connect(tmp_path / "operator-text.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")
    dispatcher = AlarmDispatcher(compiled, revision_id="r1", connection=connection)

    dispatcher.process_entity(_temperature_state("5", start), now=start)

    row = connection.execute(
        "SELECT payload_json FROM notification_outbox WHERE alarm_id = 'A2_LOLO'"
    ).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    assert payload["title"] == "Kontti"
    assert payload["message"] == (
        "Poreamme esilämmitys halvoilla tunneilla · erittäin matala\n"
        "Arvo: 5.0 °C\n\n"
        "Muut kuittaamattomat:\n"
        "• P2 · Poreamme esilämmitys halvoilla tunneilla · matala · 5.0 °C"
    )
    assert "A2_LO" not in payload["title"]
    assert "A2_LO" not in payload["message"]

    states = list_alarm_states(connection, view="active")
    assert states[0]["source_friendly_name"] == "Poreamme esilämmitys halvoilla tunneilla"
    assert states[0]["source_unit"] == "°C"

    history = list_alarm_history(connection, alarm_id="A2_LOLO")
    assert history[0]["source_friendly_name"] == "Poreamme esilämmitys halvoilla tunneilla"
    assert history[0]["source_unit"] == "°C"
    connection.close()


def test_acknowledged_alarm_is_excluded_from_notification_context(tmp_path: Path) -> None:
    start = datetime(2026, 8, 31, 9, tzinfo=UTC)
    policy = NotificationPolicyDefinition(
        policy_id="N1",
        route_key="notify.send_message",
        title="Kontti",
        target_entity_ids=("notify.jannen_puhelin",),
        locale="fi",
    )
    low_alarm = AlarmDefinition(
        alarm_id="A2_LO",
        source_tag_id="TEMP",
        kind=AlarmKind.ANALOG,
        condition="LOW",
        setpoint=20,
        priority="P2",
        category="PROCESS",
        message="",
        notification_policy_id="N1",
    )
    low_low_alarm = AlarmDefinition(
        alarm_id="A2_LOLO",
        source_tag_id="TEMP",
        kind=AlarmKind.ANALOG,
        condition="LOW_LOW",
        setpoint=10,
        priority="P2",
        category="PROCESS",
        message="",
        notification_policy_id="N1",
    )
    compiled = CompiledConfig(
        schema_version="1.0.0",
        source_hash="notification-unacknowledged-context-test",
        tags=(TagDefinition("TEMP", "sensor.temperature"),),
        alarms=(low_alarm, low_low_alarm),
        notification_policies=(policy,),
    )
    connection = connect(tmp_path / "unacknowledged-context.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")
    dispatcher = AlarmDispatcher(compiled, revision_id="r1", connection=connection)

    dispatcher.process_entity(_temperature_state("15", start), now=start)
    dispatcher.acknowledge("A2_LO", user_id="operator", now=start + timedelta(seconds=1))
    assert dispatcher.alarm_state("A2_LO").lifecycle.value == "ACTIVE_ACK"

    dispatcher.process_entity(
        _temperature_state("5", start + timedelta(seconds=2)),
        now=start + timedelta(seconds=2),
    )

    row = connection.execute(
        "SELECT payload_json FROM notification_outbox WHERE alarm_id = 'A2_LOLO'"
    ).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    assert payload["message"] == (
        "Poreamme esilämmitys halvoilla tunneilla · erittäin matala\n"
        "Arvo: 5.0 °C"
    )
    assert "Muut kuittaamattomat" not in payload["message"]
    connection.close()


def test_return_cancels_unsent_delayed_activation(tmp_path: Path) -> None:
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    policy = NotificationPolicyDefinition(
        policy_id="P1_PHONE",
        route_key="notify.mobile_app_phone",
        notify_delay_s=30,
        notify_on_return=False,
    )
    compiled = _compiled(_alarm("FAULT_1"), policy=policy)
    connection = connect(tmp_path / "cancel.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")
    dispatcher = AlarmDispatcher(compiled, revision_id="r1", connection=connection)

    dispatcher.process_entity(_state("on", start), now=start)
    pending = connection.execute(
        "SELECT available_at_utc FROM notification_outbox WHERE alarm_id = 'FAULT_1'"
    ).fetchone()
    assert pending == ((start + timedelta(seconds=30)).isoformat(),)

    dispatcher.process_entity(_state("off", start + timedelta(seconds=5)), now=start + timedelta(seconds=5))

    assert connection.execute(
        "SELECT COUNT(*) FROM notification_outbox WHERE alarm_id = 'FAULT_1'"
    ).fetchone()[0] == 0
    connection.close()


def test_suppression_cancels_delayed_activation_and_blocks_new_push(tmp_path: Path) -> None:
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    policy = NotificationPolicyDefinition(
        policy_id="P1_PHONE",
        route_key="notify.mobile_app_phone",
        notify_delay_s=30,
    )
    compiled = _compiled(_alarm("FAULT_1"), policy=policy)
    connection = connect(tmp_path / "suppressed.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")
    dispatcher = AlarmDispatcher(compiled, revision_id="r1", connection=connection)

    dispatcher.process_entity(_state("on", start), now=start)
    assert connection.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0] == 1

    set_suppressed(
        connection,
        "FAULT_1",
        suppressed=True,
        user_id="engineer",
        reason="maintenance",
        now=start + timedelta(seconds=1),
    )
    assert connection.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0] == 0

    dispatcher.process_entity(_state("off", start + timedelta(seconds=2)), now=start + timedelta(seconds=2))
    dispatcher.process_entity(_state("on", start + timedelta(seconds=3)), now=start + timedelta(seconds=3))
    assert connection.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0] == 0
    connection.close()


def test_same_update_inhibition_prevents_child_push(tmp_path: Path) -> None:
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    policy = NotificationPolicyDefinition(
        policy_id="P1_PHONE",
        route_key="notify.mobile_app_phone",
    )
    blocker = _alarm("A_BLOCKER")
    child = _alarm("B_CHILD", inhibit_by=("A_BLOCKER",))
    compiled = _compiled(blocker, child, policy=policy)
    connection = connect(tmp_path / "inhibited.db")
    apply_migrations(connection)
    store_compiled_revision(connection, compiled, revision_id="r1")
    dispatcher = AlarmDispatcher(compiled, revision_id="r1", connection=connection)

    dispatcher.process_entity(_state("on", start), now=start)

    rows = connection.execute(
        "SELECT alarm_id FROM notification_outbox ORDER BY alarm_id"
    ).fetchall()
    assert rows == [("A_BLOCKER",)]
    inhibited = connection.execute(
        "SELECT inhibited, inhibited_by_json FROM alarm_state WHERE alarm_id = 'B_CHILD'"
    ).fetchone()
    assert inhibited == (1, '["A_BLOCKER"]')
    connection.close()
