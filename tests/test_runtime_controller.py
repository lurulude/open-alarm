import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from open_alarm.backend.config.models import (
    AlarmDefinition,
    AlarmKind,
    CompiledConfig,
    TagDefinition,
)
from open_alarm.backend.domain.models import AlarmLifecycle
from open_alarm.backend.ha.client import HAConnectionStatus, HAStateUpdate
from open_alarm.backend.ha.models import normalize_entity_state
from open_alarm.backend.runtime.controller import RuntimeController
from open_alarm.backend.runtime.dispatcher import AlarmDispatcher


class FakeClient:
    def __init__(self, items: list[HAConnectionStatus | HAStateUpdate]) -> None:
        self.items = items
        self.requested: tuple[str, ...] | None = None
        self.hold = asyncio.Event()

    async def stream_states(self, entity_ids: tuple[str, ...]):
        self.requested = tuple(entity_ids)
        for item in self.items:
            yield item
        await self.hold.wait()


def _compiled(*, on_delay_s: float = 0.0) -> CompiledConfig:
    return CompiledConfig(
        schema_version="1.0.0",
        source_hash=f"controller-{on_delay_s}",
        tags=(TagDefinition("TEMP", "sensor.temp"),),
        alarms=(
            AlarmDefinition(
                alarm_id="TEMP_HI",
                source_tag_id="TEMP",
                kind=AlarmKind.ANALOG,
                condition="HIGH",
                priority="P1",
                category="PROCESS",
                setpoint=80.0,
                hysteresis=2.0,
                on_delay_s=on_delay_s,
            ),
        ),
    )


def _state(value: str, at: datetime):
    return normalize_entity_state(
        "sensor.temp",
        {"state": value, "attributes": {}},
        observed_at=at,
        source_timestamp=at,
    )


def test_controller_bridges_websocket_updates_and_stops_cleanly() -> None:
    at = datetime(2026, 8, 30, 12, tzinfo=UTC)
    dispatcher = AlarmDispatcher(_compiled(), revision_id="rev-1")
    client = FakeClient(
        [
            HAConnectionStatus(
                connected=True,
                ha_version="2026.8.3",
                subscription_mode="subscribe_entities",
            ),
            HAStateUpdate(state=_state("90", at), initial=True),
        ]
    )
    controller = RuntimeController(
        dispatcher,
        client=client,  # type: ignore[arg-type]
        tick_interval_s=60,
    )

    async def scenario() -> None:
        await controller.start()
        for _ in range(10):
            if dispatcher.alarm_state("TEMP_HI").lifecycle == AlarmLifecycle.ACTIVE_UNACK:
                break
            await asyncio.sleep(0)

        assert client.requested == ("sensor.temp",)
        assert dispatcher.alarm_state("TEMP_HI").lifecycle == AlarmLifecycle.ACTIVE_UNACK
        assert controller.status.connected is True
        assert controller.status.subscription_mode == "subscribe_entities"
        assert controller.status.last_state_at == at

        await controller.stop()
        assert controller.status.running is False
        assert controller.status.connected is False

    asyncio.run(scenario())


def test_tick_progresses_pending_alarm_while_ha_is_disconnected() -> None:
    start = datetime(2026, 8, 30, 12, tzinfo=UTC)
    dispatcher = AlarmDispatcher(_compiled(on_delay_s=10), revision_id="rev-1")
    dispatcher.process_entity(_state("90", start), now=start)
    assert dispatcher.alarm_state("TEMP_HI").lifecycle == AlarmLifecycle.PENDING_ON

    controller = RuntimeController(dispatcher, tick_interval_s=1)
    controller.handle_stream_item(
        HAConnectionStatus(connected=False, reason="Home Assistant connection lost")
    )
    controller.tick_once(now=start + timedelta(seconds=11))

    assert controller.status.connected is False
    assert controller.status.reason == "Home Assistant connection lost"
    assert controller.status.last_tick_at == start + timedelta(seconds=11)
    assert dispatcher.alarm_state("TEMP_HI").lifecycle == AlarmLifecycle.ACTIVE_UNACK


def test_status_payload_is_json_ready() -> None:
    dispatcher = AlarmDispatcher(_compiled(), revision_id="rev-1")
    controller = RuntimeController(dispatcher)
    payload: dict[str, Any] = controller.status_payload()

    assert payload["running"] is False
    assert payload["connected"] is False
    assert payload["monitored_entities"] == 1
    assert payload["last_state_at"] is None
