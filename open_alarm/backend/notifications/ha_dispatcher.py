from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..db.notification_outbox import NotificationOutboxItem
from ..ha.client import HomeAssistantWebSocketClient
from .worker import PermanentNotificationError


class NotificationRouteError(PermanentNotificationError):
    pass


class HomeAssistantNotificationDispatcher:
    def __init__(self, client: HomeAssistantWebSocketClient | None = None) -> None:
        self.client = client or HomeAssistantWebSocketClient()

    async def __call__(self, item: NotificationOutboxItem) -> None:
        domain, service = parse_notification_route(item.route_key)
        service_data, target = notification_service_call(item.payload)
        await self.client.call_service(
            domain,
            service,
            service_data=service_data,
            target=target,
        )


def parse_notification_route(route_key: str) -> tuple[str, str]:
    normalized = route_key.strip()
    domain, separator, service = normalized.partition(".")
    if separator != "." or domain != "notify" or not service.strip():
        raise NotificationRouteError(
            "notification route_key must use Home Assistant notify.<service> format"
        )
    if any(character.isspace() for character in service):
        raise NotificationRouteError("notification service name must not contain whitespace")
    return domain, service


def notification_service_call(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    targets = payload.get("_target_entity_ids")
    if targets is None or targets == []:
        return notification_service_data(payload), None
    if not isinstance(targets, list) or not all(
        isinstance(entity_id, str) and entity_id.startswith("notify.") for entity_id in targets
    ):
        raise NotificationRouteError("notification group requires one or more notify.* entities")

    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise NotificationRouteError("notification payload requires a non-empty message")
    service_data: dict[str, Any] = {"message": message.strip()}
    title = payload.get("title")
    if title is not None:
        if not isinstance(title, str):
            raise NotificationRouteError("notification title must be a string")
        service_data["title"] = title
    return service_data, {"entity_id": targets}


def notification_service_data(payload: Mapping[str, Any]) -> dict[str, Any]:
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise NotificationRouteError("notification payload requires a non-empty message")

    service_data = {
        key: value
        for key, value in payload.items()
        if not key.startswith("_")
    }
    service_data["message"] = message.strip()
    title = service_data.get("title")
    if title is not None and not isinstance(title, str):
        raise NotificationRouteError("notification title must be a string")
    data = service_data.get("data")
    if data is not None and not isinstance(data, Mapping):
        raise NotificationRouteError("notification data must be an object")
    if isinstance(data, Mapping):
        service_data["data"] = dict(data)
    return service_data
