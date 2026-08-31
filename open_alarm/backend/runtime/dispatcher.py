from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from sqlite3 import Connection

from ..config.models import AlarmDefinition, AlarmKind, CompiledConfig, NotificationPolicyDefinition
from ..db.alarm_control_repository import set_automatic_inhibition
from ..db.runtime_repository import load_alarm_runtime, save_alarm_runtime
from ..domain.digital import DigitalQualifierState, qualify_digital
from ..domain.engine import (
    AlarmRuntimeState,
    EngineResult,
    acknowledge,
    process_condition,
    reset_latched,
)
from ..domain.evaluation import analog_is_abnormal
from ..domain.models import AlarmLifecycle, AlarmPolicy, AnalogCondition, AnalogRule, DigitalRule
from ..ha.models import EntityQuality, HAEntityState
from .tag_manager import TagManager


@dataclass(slots=True)
class AlarmInstance:
    definition: AlarmDefinition
    state: AlarmRuntimeState
    digital: DigitalQualifierState | None = None
    inhibited_by: tuple[str, ...] = ()
    source_friendly_name: str | None = None
    source_unit: str | None = None


class AlarmDispatcher:
    def __init__(
        self,
        compiled: CompiledConfig,
        *,
        revision_id: str,
        connection: Connection | None = None,
    ) -> None:
        self.compiled = compiled
        self.revision_id = revision_id
        self.connection = connection
        self.tags = TagManager(compiled.tags)
        self._notification_policies: dict[str, NotificationPolicyDefinition] = {
            policy.policy_id: policy for policy in compiled.notification_policies if policy.enabled
        }
        self._alarms: dict[str, AlarmInstance] = {}
        self._alarms_by_tag: dict[str, list[str]] = {}

        for definition in compiled.alarms:
            if not definition.enabled:
                continue
            instance = self._load_instance(definition)
            self._alarms[definition.alarm_id] = instance
            self._alarms_by_tag.setdefault(definition.source_tag_id, []).append(definition.alarm_id)

    def _load_instance(self, definition: AlarmDefinition) -> AlarmInstance:
        digital = definition.kind == AlarmKind.DIGITAL
        if self.connection is not None:
            persisted = load_alarm_runtime(
                self.connection,
                revision_id=self.revision_id,
                alarm_id=definition.alarm_id,
                digital=digital,
            )
            if persisted is not None:
                return AlarmInstance(
                    definition=definition,
                    state=persisted.state,
                    digital=persisted.digital,
                    inhibited_by=persisted.inhibited_by,
                    source_friendly_name=persisted.source_friendly_name,
                    source_unit=persisted.source_unit,
                )

        return AlarmInstance(
            definition=definition,
            state=AlarmRuntimeState(),
            digital=DigitalQualifierState() if digital else None,
        )

    @property
    def monitored_entity_ids(self) -> tuple[str, ...]:
        return self.tags.monitored_entity_ids

    def alarm_state(self, alarm_id: str) -> AlarmRuntimeState:
        return self._alarms[alarm_id].state

    def alarm_inhibited_by(self, alarm_id: str) -> tuple[str, ...]:
        return self._alarms[alarm_id].inhibited_by

    def initialize_missing(self, *, now: datetime | None = None) -> tuple[EngineResult, ...]:
        current_time = now or datetime.now(UTC)
        changed_tags = self.tags.initialize_missing(now=current_time)
        results: list[EngineResult] = []
        for change in changed_tags:
            results.extend(self._evaluate_tag(change.tag_id, change.current, current_time))
        self._refresh_inhibition(now=current_time)
        return tuple(results)

    def process_entity(
        self,
        state: HAEntityState,
        *,
        now: datetime | None = None,
    ) -> tuple[EngineResult, ...]:
        current_time = now or state.observed_at
        changes = self.tags.update_entity(state, now=current_time)
        results: list[EngineResult] = []
        for change in changes:
            results.extend(self._evaluate_tag(change.tag_id, change.current, current_time))
        self._refresh_inhibition(now=current_time)
        return tuple(results)

    def tick(self, *, now: datetime | None = None) -> tuple[EngineResult, ...]:
        current_time = now or datetime.now(UTC)
        results: list[EngineResult] = []
        evaluated_alarm_ids: set[str] = set()

        for change in self.tags.refresh_stale(now=current_time):
            alarm_ids = self._alarms_by_tag.get(change.tag_id, ())
            results.extend(self._evaluate_tag(change.tag_id, change.current, current_time))
            evaluated_alarm_ids.update(alarm_ids)

        for alarm_id, instance in self._alarms.items():
            if alarm_id in evaluated_alarm_ids or not self._timer_due(instance, current_time):
                continue
            tag_state = self.tags.get(instance.definition.source_tag_id)
            if tag_state is None:
                continue
            result = self._evaluate_alarm(instance, tag_state, current_time)
            if result is not None:
                results.append(result)

        self._refresh_inhibition(now=current_time)
        return tuple(results)

    def acknowledge(
        self,
        alarm_id: str,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> EngineResult:
        current_time = now or datetime.now(UTC)
        instance = self._alarms[alarm_id]
        result = acknowledge(instance.state, now=current_time)
        self._persist(
            instance,
            result,
            raw_value=None,
            qualified_value=instance.state.condition_abnormal,
            user_id=user_id,
            now=current_time,
        )
        self._refresh_inhibition(now=current_time)
        return result

    def reset(
        self,
        alarm_id: str,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> EngineResult:
        current_time = now or datetime.now(UTC)
        instance = self._alarms[alarm_id]
        if not instance.definition.latching:
            raise ValueError("alarm is not configured as latching")
        result = reset_latched(instance.state, now=current_time)
        self._persist(
            instance,
            result,
            raw_value=None,
            qualified_value=False,
            user_id=user_id,
            now=current_time,
        )
        self._refresh_inhibition(now=current_time)
        return result

    def _evaluate_tag(
        self,
        tag_id: str,
        tag_state: HAEntityState,
        now: datetime,
    ) -> list[EngineResult]:
        results: list[EngineResult] = []
        for alarm_id in self._alarms_by_tag.get(tag_id, []):
            instance = self._alarms[alarm_id]
            result = self._evaluate_alarm(instance, tag_state, now)
            if result is not None:
                results.append(result)
        return results

    def _evaluate_alarm(
        self,
        instance: AlarmInstance,
        tag_state: HAEntityState,
        now: datetime,
    ) -> EngineResult | None:
        self._update_source_metadata(instance, tag_state)
        definition = instance.definition

        if definition.kind != AlarmKind.DEVICE and tag_state.quality != EntityQuality.GOOD:
            return self._handle_bad_quality(instance, tag_state, now)

        if definition.kind == AlarmKind.ANALOG:
            return self._evaluate_analog(instance, tag_state, now)
        if definition.kind == AlarmKind.DIGITAL:
            return self._evaluate_digital(instance, tag_state, now)
        if definition.kind == AlarmKind.DEVICE:
            return self._evaluate_device(instance, tag_state, now)
        raise ValueError(f"unsupported alarm kind: {definition.kind}")

    @staticmethod
    def _update_source_metadata(instance: AlarmInstance, tag_state: HAEntityState) -> None:
        friendly_name = tag_state.attributes.get("friendly_name")
        if isinstance(friendly_name, str) and friendly_name.strip():
            instance.source_friendly_name = friendly_name.strip()
        unit = tag_state.attributes.get("unit_of_measurement")
        if isinstance(unit, str) and unit.strip():
            instance.source_unit = unit.strip()
        elif "unit_of_measurement" in tag_state.attributes:
            instance.source_unit = None

    def _handle_bad_quality(
        self,
        instance: AlarmInstance,
        tag_state: HAEntityState,
        now: datetime,
    ) -> EngineResult:
        digital_changed = False
        if instance.digital is not None:
            before_digital = self._digital_signature(instance.digital)
            instance.digital.pending_target = None
            instance.digital.pending_started_at = None
            instance.digital.pending_deadline = None
            digital_changed = before_digital != self._digital_signature(instance.digital)

        lifecycle = instance.state.lifecycle
        if lifecycle == AlarmLifecycle.PENDING_ON:
            result = process_condition(
                instance.state,
                abnormal=False,
                policy=self._policy(instance.definition),
                now=now,
            )
        elif lifecycle == AlarmLifecycle.PENDING_OFF:
            result = process_condition(
                instance.state,
                abnormal=True,
                policy=self._policy(instance.definition),
                now=now,
            )
        else:
            result = EngineResult(instance.state, [])

        self._persist(
            instance,
            result,
            raw_value=tag_state.state,
            qualified_value=instance.state.condition_abnormal,
            now=now,
            force=digital_changed,
        )
        return result

    def _evaluate_analog(
        self,
        instance: AlarmInstance,
        tag_state: HAEntityState,
        now: datetime,
    ) -> EngineResult:
        definition = instance.definition
        try:
            value = float(tag_state.state) if tag_state.state is not None else None
        except ValueError:
            value = None

        if value is None:
            return self._handle_bad_quality(instance, tag_state, now)

        previously_abnormal = (
            instance.state.returned_at is None
            and instance.state.lifecycle
            in {
                AlarmLifecycle.ACTIVE_UNACK,
                AlarmLifecycle.ACTIVE_ACK,
                AlarmLifecycle.PENDING_OFF,
            }
        )
        rule = AnalogRule(
            condition=AnalogCondition(definition.condition),
            setpoint=float(definition.setpoint),
            hysteresis=definition.hysteresis,
            on_delay_s=definition.on_delay_s,
            off_delay_s=definition.off_delay_s,
        )
        abnormal = analog_is_abnormal(rule, value, previously_abnormal)
        result = process_condition(
            instance.state,
            abnormal=abnormal,
            policy=self._policy(definition),
            now=now,
        )
        self._persist(
            instance,
            result,
            raw_value=value,
            qualified_value=abnormal,
            now=now,
        )
        return result

    def _evaluate_digital(
        self,
        instance: AlarmInstance,
        tag_state: HAEntityState,
        now: datetime,
    ) -> EngineResult:
        definition = instance.definition
        digital = instance.digital
        if digital is None:
            raise RuntimeError("digital alarm has no qualifier state")

        if definition.condition == "EQUALS":
            raw_alarm = tag_state.state == definition.alarm_value
        elif definition.condition == "NOT_EQUALS":
            raw_alarm = tag_state.state != definition.alarm_value
        else:
            raise ValueError(f"unsupported digital condition: {definition.condition}")

        rule = DigitalRule(
            alarm_value=str(definition.alarm_value),
            debounce_on_s=definition.debounce_on_s,
            debounce_off_s=definition.debounce_off_s,
            on_delay_s=definition.on_delay_s,
            off_delay_s=definition.off_delay_s,
        )
        before_digital = self._digital_signature(digital)
        qualified = qualify_digital(digital, raw_alarm=raw_alarm, rule=rule, now=now)
        digital_changed = before_digital != self._digital_signature(digital)
        result = process_condition(
            instance.state,
            abnormal=qualified,
            policy=self._policy(definition),
            now=now,
        )
        self._persist(
            instance,
            result,
            raw_value=raw_alarm,
            qualified_value=qualified,
            now=now,
            force=digital_changed,
        )
        return result

    def _evaluate_device(
        self,
        instance: AlarmInstance,
        tag_state: HAEntityState,
        now: datetime,
    ) -> EngineResult:
        condition = instance.definition.condition
        if condition == "BAD_QUALITY":
            abnormal = tag_state.quality != EntityQuality.GOOD
        else:
            abnormal = tag_state.quality.value == condition

        result = process_condition(
            instance.state,
            abnormal=abnormal,
            policy=self._policy(instance.definition),
            now=now,
        )
        self._persist(
            instance,
            result,
            raw_value=tag_state.quality.value,
            qualified_value=abnormal,
            now=now,
        )
        return result

    @staticmethod
    def _timer_due(instance: AlarmInstance, now: datetime) -> bool:
        lifecycle_pending = (
            instance.state.lifecycle in {AlarmLifecycle.PENDING_ON, AlarmLifecycle.PENDING_OFF}
            and instance.state.pending_deadline is not None
            and now >= instance.state.pending_deadline
        )
        digital = instance.digital
        debounce_pending = (
            digital is not None
            and digital.pending_target is not None
            and digital.pending_deadline is not None
            and now >= digital.pending_deadline
        )
        return lifecycle_pending or debounce_pending

    @staticmethod
    def _digital_signature(
        digital: DigitalQualifierState,
    ) -> tuple[bool, bool, bool | None, datetime | None, datetime | None]:
        return (
            digital.raw_alarm,
            digital.qualified_alarm,
            digital.pending_target,
            digital.pending_started_at,
            digital.pending_deadline,
        )

    def _refresh_inhibition(self, *, now: datetime) -> None:
        for alarm_id, instance in self._alarms.items():
            inhibited_by = tuple(
                sorted(
                    inhibitor_id
                    for inhibitor_id in instance.definition.inhibit_by_alarm_ids
                    if self._inhibitor_asserted(inhibitor_id)
                )
            )
            if inhibited_by == instance.inhibited_by:
                continue

            previous = instance.inhibited_by
            if self.connection is not None:
                try:
                    set_automatic_inhibition(
                        self.connection,
                        alarm_id,
                        inhibited_by=inhibited_by,
                        now=now,
                    )
                except KeyError:
                    save_alarm_runtime(
                        self.connection,
                        revision_id=self.revision_id,
                        alarm_id=alarm_id,
                        result=EngineResult(instance.state, []),
                        digital=instance.digital,
                        raw_value=None,
                        qualified_value=instance.state.condition_abnormal,
                        inhibited_by=previous,
                        message=instance.definition.message,
                        priority=instance.definition.priority,
                        notification_policy=self._notification_policy(instance.definition),
                        source_friendly_name=instance.source_friendly_name,
                        source_unit=instance.source_unit,
                        now=now,
                    )
                    set_automatic_inhibition(
                        self.connection,
                        alarm_id,
                        inhibited_by=inhibited_by,
                        now=now,
                    )
            instance.inhibited_by = inhibited_by

    def _inhibitor_asserted(self, alarm_id: str) -> bool:
        inhibitor = self._alarms.get(alarm_id)
        if inhibitor is None:
            return False
        return inhibitor.state.condition_abnormal and inhibitor.state.lifecycle in {
            AlarmLifecycle.ACTIVE_UNACK,
            AlarmLifecycle.ACTIVE_ACK,
        }

    @staticmethod
    def _policy(definition: AlarmDefinition) -> AlarmPolicy:
        return AlarmPolicy(
            on_delay_s=definition.on_delay_s,
            off_delay_s=definition.off_delay_s,
            rtn_ack_required=definition.rtn_ack_required,
            latching=definition.latching,
        )

    def _notification_policy(
        self,
        definition: AlarmDefinition,
    ) -> NotificationPolicyDefinition | None:
        if definition.notification_policy_id is None:
            return None
        return self._notification_policies.get(definition.notification_policy_id)

    def _persist(
        self,
        instance: AlarmInstance,
        result: EngineResult,
        *,
        raw_value: object,
        qualified_value: object,
        user_id: str | None = None,
        now: datetime,
        force: bool = False,
    ) -> None:
        if self.connection is None:
            return
        if (
            not force
            and not result.events
            and result.state.lifecycle == AlarmLifecycle.NORMAL
            and not result.state.condition_abnormal
            and not result.state.latched
        ):
            return
        save_alarm_runtime(
            self.connection,
            revision_id=self.revision_id,
            alarm_id=instance.definition.alarm_id,
            result=result,
            digital=instance.digital,
            raw_value=raw_value,
            qualified_value=qualified_value,
            inhibited_by=instance.inhibited_by,
            user_id=user_id,
            message=instance.definition.message,
            priority=instance.definition.priority,
            notification_policy=self._notification_policy(instance.definition),
            source_friendly_name=instance.source_friendly_name,
            source_unit=instance.source_unit,
            now=now,
        )
