from datetime import UTC, datetime
from pathlib import Path

from open_alarm.backend.config.models import NotificationPolicyDefinition
from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.domain.models import AlarmEventType
from open_alarm.backend.ha.event_client import HAEvent, event_from_subscription_message
from open_alarm.backend.notifications.actions import NotificationActionListener
from open_alarm.backend.notifications.constants import ACK_ACTION_PREFIX
from open_alarm.backend.notifications.router import build_notification_payload

SYSTEM_ALARM_ID = "SYS_HA_CONNECTION_LOST"
NOW = datetime(2026, 8, 30, 17, tzinfo=UTC)


def _connection(tmp_path: Path, *, role: str = "OPERATOR"):
    connection = connect(tmp_path / "actions.db")
    apply_migrations(connection)
    timestamp = NOW.isoformat()
    with connection:
        connection.execute(
            """
            INSERT INTO app_user(
                user_id, user_name, display_name, role, locale,
                created_at_utc, updated_at_utc, last_seen_at_utc
            ) VALUES ('user-1', 'operator', 'Operator', ?, 'en', ?, ?, ?)
            """,
            (role, timestamp, timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO alarm_state(
                alarm_id, revision_id, origin, lifecycle, condition_abnormal, updated_at_utc
            ) VALUES (?, NULL, 'SYSTEM', 'ACTIVE_UNACK', 1, ?)
            """,
            (SYSTEM_ALARM_ID, timestamp),
        )
    return connection


def _activation_event(connection, *, event_type: str = "ACTIVATE") -> int:
    with connection:
        cursor = connection.execute(
            """
            INSERT INTO alarm_event(
                alarm_id, revision_id, origin, event_type, event_at_utc, message
            ) VALUES (?, NULL, 'SYSTEM', ?, ?, 'Home Assistant connection lost')
            """,
            (SYSTEM_ALARM_ID, event_type, NOW.isoformat()),
        )
    return int(cursor.lastrowid)


def _action(event_id: int, *, user_id: str | None = "user-1") -> HAEvent:
    return HAEvent(
        event_type="mobile_app_notification_action",
        data={"action": f"{ACK_ACTION_PREFIX}{event_id}"},
        time_fired=NOW,
        context_user_id=user_id,
    )


def test_operator_can_acknowledge_latest_alarm_from_notification(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    event_id = _activation_event(connection)
    listener = NotificationActionListener(connection, runtime_provider=lambda: None)

    assert listener.handle_event(_action(event_id)) is True
    state = connection.execute(
        "SELECT lifecycle FROM alarm_state WHERE alarm_id = ?",
        (SYSTEM_ALARM_ID,),
    ).fetchone()
    ack = connection.execute(
        """
        SELECT event_type, user_id
        FROM alarm_event
        WHERE alarm_id = ?
        ORDER BY event_id DESC LIMIT 1
        """,
        (SYSTEM_ALARM_ID,),
    ).fetchone()
    assert state == ("ACTIVE_ACK",)
    assert ack == ("ACK", "user-1")
    assert listener.handle_event(_action(event_id)) is False
    connection.close()


def test_viewer_cannot_acknowledge_from_notification(tmp_path: Path) -> None:
    connection = _connection(tmp_path, role="VIEWER")
    event_id = _activation_event(connection)
    listener = NotificationActionListener(connection, runtime_provider=lambda: None)

    assert listener.handle_event(_action(event_id)) is False
    state = connection.execute(
        "SELECT lifecycle FROM alarm_state WHERE alarm_id = ?",
        (SYSTEM_ALARM_ID,),
    ).fetchone()
    assert state == ("ACTIVE_UNACK",)
    connection.close()


def test_stale_activation_action_cannot_acknowledge_new_occurrence(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    stale_event_id = _activation_event(connection)
    _activation_event(connection, event_type="REACTIVATE")
    listener = NotificationActionListener(connection, runtime_provider=lambda: None)

    assert listener.handle_event(_action(stale_event_id)) is False
    state = connection.execute(
        "SELECT lifecycle FROM alarm_state WHERE alarm_id = ?",
        (SYSTEM_ALARM_ID,),
    ).fetchone()
    assert state == ("ACTIVE_UNACK",)
    connection.close()


def test_mobile_event_parser_preserves_home_assistant_user_context() -> None:
    event = event_from_subscription_message(
        {
            "id": 4,
            "type": "event",
            "event": {
                "event_type": "mobile_app_notification_action",
                "data": {"action": "OPEN_ALARM_ACK_42"},
                "time_fired": "2026-08-30T17:00:00+00:00",
                "context": {"id": "context-1", "user_id": "user-1"},
            },
        },
        subscription_id=4,
        expected_event_type="mobile_app_notification_action",
    )

    assert event is not None
    assert event.context_user_id == "user-1"
    assert event.data["action"] == "OPEN_ALARM_ACK_42"
    assert event.time_fired == NOW


def test_active_notification_has_localized_secure_actions() -> None:
    payload = build_notification_payload(
        alarm_id="TEMP.HI",
        event_id=42,
        event_type=AlarmEventType.ACTIVATE,
        priority="P1",
        message="Lämpötila korkea",
        raw_value=91.2,
        policy=NotificationPolicyDefinition(
            policy_id="PHONE_FI",
            route_key="notify.mobile_app_phone",
            locale="fi",
        ),
    )

    actions = payload["data"]["actions"]
    assert actions[0] == {
        "action": "OPEN_ALARM_ACK_42",
        "title": "Kuittaa",
        "authenticationRequired": True,
    }
    assert actions[1]["action"] == "URI"
    assert actions[1]["title"] == "Avaa hälytykset"
    assert payload["data"]["url"] == "/hassio/ingress/open_alarm"
