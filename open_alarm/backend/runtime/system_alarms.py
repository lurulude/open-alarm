from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from sqlite3 import Connection
from typing import Any

from ..db.runtime_repository import load_alarm_runtime, save_alarm_runtime
from ..domain.engine import AlarmRuntimeState, EngineResult, acknowledge, process_condition
from ..domain.models import AlarmLifecycle, AlarmPolicy

SYSTEM_ORIGIN = "SYSTEM"
HA_CONNECTION_ALARM_ID = "SYS_HA_CONNECTION_LOST"
RUNTIME_CONFIG_ALARM_ID = "SYS_RUNTIME_CONFIG_ERROR"
NOTIFICATION_WORKER_ALARM_ID = "SYS_NOTIFICATION_WORKER_STOPPED"
NOTIFICATION_DELIVERY_ALARM_ID = "SYS_NOTIFICATION_DELIVERY_FAILED"
HA_CONNECTION_ON_DELAY_S = 5.0


@dataclass(frozen=True, slots=True)
class SystemAlarmDefinition:
    alarm_id: str
    priority: str
    category: str
    message: str
    message_key: str
    policy: AlarmPolicy = field(default_factory=AlarmPolicy)


SYSTEM_ALARM_DEFINITIONS: dict[str, SystemAlarmDefinition] = {
    HA_CONNECTION_ALARM_ID: SystemAlarmDefinition(
        alarm_id=HA_CONNECTION_ALARM_ID,
        priority="P1",
        category="SYSTEM",
        message="Home Assistant connection lost",
        message_key="alarm.system.SYS_HA_CONNECTION_LOST",
        policy=AlarmPolicy(on_delay_s=HA_CONNECTION_ON_DELAY_S),
    ),
    RUNTIME_CONFIG_ALARM_ID: SystemAlarmDefinition(
        alarm_id=RUNTIME_CONFIG_ALARM_ID,
        priority="P1",
        category="SYSTEM",
        message="Active configuration could not be loaded",
        message_key="alarm.system.SYS_RUNTIME_CONFIG_ERROR",
    ),
    NOTIFICATION_WORKER_ALARM_ID: SystemAlarmDefinition(
        alarm_id=NOTIFICATION_WORKER_ALARM_ID,
        priority="P1",
        category="SYSTEM",
        message="Notification delivery worker stopped",
        message_key="alarm.system.SYS_NOTIFICATION_WORKER_STOPPED",
    ),
    NOTIFICATION_DELIVERY_ALARM_ID: SystemAlarmDefinition(
        alarm_id=NOTIFICATION_DELIVERY_ALARM_ID,
        priority="P2",
        category="SYSTEM",
        message="Notification delivery failed",
        message_key="alarm.system.SYS_NOTIFICATION_DELIVERY_FAILED",
    ),
}


class SystemAlarmManager:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self._states: dict[str, AlarmRuntimeState] = {}
        self._conditions: dict[str, bool] = {}
        self._raw_values: dict[str, Any] = {}
        for alarm_id in SYSTEM_ALARM_DEFINITIONS:
            persisted = load_alarm_runtime(
                connection,
                revision_id=None,
                alarm_id=alarm_id,
                digital=False,
                origin=SYSTEM_ORIGIN,
            )
            state = persisted.state if persisted is not None else AlarmRuntimeState()
            self._states[alarm_id] = state
            self._conditions[alarm_id] = state.condition_abnormal
            self._raw_values[alarm_id] = None if persisted is None else persisted.raw_value

        self._ha_connected = not self._conditions[HA_CONNECTION_ALARM_ID]
        self._ha_reason: str | None = None

    def alarm_state(self, alarm_id: str) -> AlarmRuntimeState:
        try:
            return self._states[alarm_id]
        except KeyError as exc:
            raise KeyError(alarm_id) from exc

    def set_condition(
        self,
        alarm_id: str,
        abnormal: bool,
        *,
        raw_value: Any = None,
        now: datetime | None = None,
    ) -> EngineResult:
        definition = self._definition(alarm_id)
        state = self._states[alarm_id]
        if self._conditions[alarm_id] == abnormal and self._raw_values[alarm_id] == raw_value:
            return EngineResult(state=state)

        timestamp = now or datetime.now(UTC)
        self._conditions[alarm_id] = abnormal
        self._raw_values[alarm_id] = raw_value
        result = process_condition(
            state,
            abnormal=abnormal,
            policy=definition.policy,
            now=timestamp,
        )
        self._persist(alarm_id, result, timestamp)
        return result

    def set_ha_connected(
        self,
        connected: bool,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> EngineResult:
        timestamp = now or datetime.now(UTC)
        changed = connected != self._ha_connected
        self._ha_connected = connected
        self._ha_reason = reason
        if changed:
            self.record_runtime_event(
                "HA_CONNECTED" if connected else "HA_DISCONNECTED",
                at=timestamp,
                details={"reason": reason} if reason else None,
            )
        return self.set_condition(
            HA_CONNECTION_ALARM_ID,
            not connected,
            raw_value={"connected": connected, "reason": reason},
            now=timestamp,
        )

    def tick(self, *, now: datetime | None = None) -> tuple[EngineResult, ...]:
        timestamp = now or datetime.now(UTC)
        results: list[EngineResult] = []
        for alarm_id, state in self._states.items():
            if state.lifecycle not in {AlarmLifecycle.PENDING_ON, AlarmLifecycle.PENDING_OFF}:
                continue
            definition = self._definition(alarm_id)
            result = process_condition(
                state,
                abnormal=self._conditions[alarm_id],
                policy=definition.policy,
                now=timestamp,
            )
            self._persist(alarm_id, result, timestamp)
            results.append(result)
        return tuple(results)

    def acknowledge(
        self,
        alarm_id: str,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> EngineResult:
        self._definition(alarm_id)
        timestamp = now or datetime.now(UTC)
        result = acknowledge(self._states[alarm_id], now=timestamp)
        self._persist(alarm_id, result, timestamp, user_id=user_id)
        return result

    def record_runtime_event(
        self,
        event_type: str,
        *,
        at: datetime | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        timestamp = (at or datetime.now(UTC)).astimezone(UTC).isoformat()
        with self.connection:
            self.connection.execute(
                "INSERT INTO runtime_event(event_type, event_at_utc, details_json) VALUES (?, ?, ?)",
                (
                    event_type,
                    timestamp,
                    None
                    if details is None
                    else json.dumps(details, sort_keys=True, separators=(",", ":")),
                ),
            )

    def _definition(self, alarm_id: str) -> SystemAlarmDefinition:
        try:
            return SYSTEM_ALARM_DEFINITIONS[alarm_id]
        except KeyError as exc:
            raise KeyError(alarm_id) from exc

    def _persist(
        self,
        alarm_id: str,
        result: EngineResult,
        now: datetime,
        *,
        user_id: str | None = None,
    ) -> None:
        definition = self._definition(alarm_id)
        save_alarm_runtime(
            self.connection,
            revision_id=None,
            alarm_id=alarm_id,
            result=result,
            raw_value=self._raw_values[alarm_id],
            qualified_value=self._conditions[alarm_id],
            user_id=user_id,
            message=definition.message,
            now=now,
            origin=SYSTEM_ORIGIN,
        )

    @property
    def active_or_pending(self) -> bool:
        return any(state.lifecycle != AlarmLifecycle.NORMAL for state in self._states.values())
