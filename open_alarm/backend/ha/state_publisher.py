from __future__ import annotations

import asyncio
import json
import logging
import os
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_CORE_API_URL = "http://supervisor/core/api"
UNACKNOWLEDGED_ENTITY_ID = "sensor.open_alarm_unacknowledged"
ATTENTION_ENTITY_ID = "binary_sensor.open_alarm_attention"

_LOGGER = logging.getLogger(__name__)


class HomeAssistantAlarmStatePublisher:
    def __init__(
        self,
        *,
        api_url: str = DEFAULT_CORE_API_URL,
        token: str | None = None,
        request_timeout_s: float = 5.0,
        retry_interval_s: float = 15.0,
        heartbeat_s: float = 30.0,
    ) -> None:
        if request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be > 0")
        if retry_interval_s < 0:
            raise ValueError("retry_interval_s must be >= 0")
        if heartbeat_s <= 0:
            raise ValueError("heartbeat_s must be > 0")
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.request_timeout_s = request_timeout_s
        self.retry_interval_s = retry_interval_s
        self.heartbeat_s = heartbeat_s
        self._last_published_count: int | None = None
        self._last_success_at: float | None = None
        self._last_attempt_count: int | None = None
        self._last_attempt_at: float | None = None
        self._failure_logged = False

    async def publish(self, unacknowledged: int) -> bool:
        if unacknowledged < 0:
            raise ValueError("unacknowledged must be >= 0")

        now = monotonic()
        if (
            self._last_published_count == unacknowledged
            and self._last_success_at is not None
            and now - self._last_success_at < self.heartbeat_s
        ):
            return True
        if (
            self._last_attempt_count == unacknowledged
            and self._last_attempt_at is not None
            and self._last_published_count != unacknowledged
            and now - self._last_attempt_at < self.retry_interval_s
        ):
            return False

        self._last_attempt_count = unacknowledged
        self._last_attempt_at = now
        try:
            await asyncio.to_thread(self._publish_sync, unacknowledged)
        except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
            if not self._failure_logged:
                _LOGGER.warning("Cannot publish Open Alarm Home Assistant states: %s", exc)
                self._failure_logged = True
            return False

        if self._failure_logged:
            _LOGGER.info("Open Alarm Home Assistant state publication recovered")
        self._failure_logged = False
        self._last_published_count = unacknowledged
        self._last_success_at = monotonic()
        return True

    async def publish_unavailable(self) -> bool:
        try:
            await asyncio.to_thread(self._publish_unavailable_sync)
        except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
            if not self._failure_logged:
                _LOGGER.warning("Cannot mark Open Alarm Home Assistant states unavailable: %s", exc)
                self._failure_logged = True
            return False
        self._last_published_count = None
        self._last_success_at = None
        return True

    def _resolve_token(self) -> str:
        token = self.token or os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise ValueError("SUPERVISOR_TOKEN is not available")
        return token

    def _publish_sync(self, unacknowledged: int) -> None:
        self._post_state(
            UNACKNOWLEDGED_ENTITY_ID,
            str(unacknowledged),
            {
                "friendly_name": "Open Alarm unacknowledged",
                "icon": "mdi:alert",
            },
        )
        self._post_state(
            ATTENTION_ENTITY_ID,
            "on" if unacknowledged > 0 else "off",
            {
                "friendly_name": "Open Alarm attention",
                "device_class": "problem",
                "icon": "mdi:alert",
                "unacknowledged": unacknowledged,
            },
        )

    def _publish_unavailable_sync(self) -> None:
        self._post_state(
            UNACKNOWLEDGED_ENTITY_ID,
            "unavailable",
            {
                "friendly_name": "Open Alarm unacknowledged",
                "icon": "mdi:alert",
            },
        )
        self._post_state(
            ATTENTION_ENTITY_ID,
            "unavailable",
            {
                "friendly_name": "Open Alarm attention",
                "device_class": "problem",
                "icon": "mdi:alert",
            },
        )

    def _post_state(self, entity_id: str, state: str, attributes: dict[str, object]) -> None:
        body = json.dumps(
            {"state": state, "attributes": attributes},
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{self.api_url}/states/{entity_id}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._resolve_token()}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=self.request_timeout_s) as response:
            if response.status not in {200, 201}:
                raise OSError(f"Home Assistant state publish failed with HTTP {response.status}")
