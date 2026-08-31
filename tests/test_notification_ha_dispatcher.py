import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from open_alarm.backend.db.notification_outbox import NotificationOutboxItem
from open_alarm.backend.notifications.ha_dispatcher import (
    HomeAssistantNotificationDispatcher,
    NotificationRouteError,
    notification_service_call,
    notification_service_data,
    parse_notification_route,
)


class FakeHAClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, Any] | None]] = []

    async def call_service(
        self,
        domain: str,
        service: str,
        *,
        service_data=None,
        target=None,
        return_response: bool = False,
    ) -> None:
        del return_response
        self.calls.append(
            (
                domain,
                service,
                dict(service_data or {}),
                None if target is None else dict(target),
            )
        )


def _item(*, route_key: str = "notify.mobile_app_phone", payload=None) -> NotificationOutboxItem:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    return NotificationOutboxItem(
        outbox_id=1,
        dedupe_key="TEMP_HI:ACTIVATE:phone",
        alarm_id="TEMP_HI",
        revision_id="rev-1",
        origin="ENGINEERING",
        event_type="ACTIVATE",
        route_key=route_key,
        payload=payload or {"title": "Open Alarm", "message": "Temperature high"},
        status="PROCESSING",
        attempts=1,
        available_at=now,
        locked_at=now,
        created_at=now,
    )


def test_dispatcher_keeps_legacy_notify_service_payload() -> None:
    client = FakeHAClient()
    dispatcher = HomeAssistantNotificationDispatcher(client)  # type: ignore[arg-type]

    asyncio.run(dispatcher(_item(payload={
        "title": "Open Alarm",
        "message": " Temperature high ",
        "data": {"tag": "TEMP_HI", "group": "open_alarm"},
    })))

    assert client.calls == [
        (
            "notify",
            "mobile_app_phone",
            {
                "title": "Open Alarm",
                "message": "Temperature high",
                "data": {"tag": "TEMP_HI", "group": "open_alarm"},
            },
            None,
        )
    ]


def test_dispatcher_targets_multiple_notify_entities_with_send_message() -> None:
    client = FakeHAClient()
    dispatcher = HomeAssistantNotificationDispatcher(client)  # type: ignore[arg-type]

    asyncio.run(dispatcher(_item(
        route_key="notify.send_message",
        payload={
            "title": "Open Alarm · ALARM · P2",
            "message": "Temperature high\nValue: 95",
            "data": {"tag": "TEMP_HI", "actions": [{"action": "ignored"}]},
            "_target_entity_ids": ["notify.jannen_puhelin", "notify.wall_tablet"],
        },
    )))

    assert client.calls == [
        (
            "notify",
            "send_message",
            {
                "title": "Open Alarm · ALARM · P2",
                "message": "Temperature high\nValue: 95",
            },
            {"entity_id": ["notify.jannen_puhelin", "notify.wall_tablet"]},
        )
    ]


def test_notification_route_requires_notify_service_format() -> None:
    assert parse_notification_route("notify.send_message") == ("notify", "send_message")
    with pytest.raises(NotificationRouteError):
        parse_notification_route("mobile_app_phone")
    with pytest.raises(NotificationRouteError):
        parse_notification_route("script.open_alarm")


def test_notification_payload_requires_message_and_structured_data() -> None:
    with pytest.raises(NotificationRouteError, match="message"):
        notification_service_data({"title": "Missing message"})
    with pytest.raises(NotificationRouteError, match="data"):
        notification_service_data({"message": "Alarm", "data": "invalid"})


def test_notification_group_requires_notify_entities() -> None:
    with pytest.raises(NotificationRouteError, match=r"notify\.\* entities"):
        notification_service_call({
            "message": "Alarm",
            "_target_entity_ids": ["sensor.notifier"],
        })
