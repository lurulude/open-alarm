from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AlarmKind(str, Enum):
    ANALOG = "ANALOG"
    DIGITAL = "DIGITAL"
    DEVICE = "DEVICE"


class IssueSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True, slots=True)
class TagDefinition:
    tag_id: str
    entity_id: str
    value_type: str = "auto"
    stale_after_s: float | None = None
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class NotificationPolicyDefinition:
    policy_id: str
    route_key: str
    display_name: str = ""
    title: str = "Open Alarm"
    target_entity_ids: tuple[str, ...] = ()
    notify_on_active: bool = True
    notify_on_return: bool = False
    notify_on_ack: bool = False
    notify_delay_s: float = 0.0
    notification_channel: str | None = None
    notification_group: str | None = "open_alarm"
    critical: bool = False
    locale: str = "en"
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_entity_ids", tuple(self.target_entity_ids))
        if not self.title.strip():
            raise ValueError("notification title must not be empty")
        if self.locale not in {"en", "fi"}:
            raise ValueError("notification locale must be 'en' or 'fi'")


@dataclass(frozen=True, slots=True)
class AlarmDefinition:
    alarm_id: str
    source_tag_id: str
    kind: AlarmKind
    condition: str
    priority: str
    category: str
    alarm_group_id: str | None = None
    message: str = ""
    message_fi: str = ""
    setpoint: float | None = None
    hysteresis: float = 0.0
    alarm_value: str | None = None
    debounce_on_s: float = 0.0
    debounce_off_s: float = 0.0
    on_delay_s: float = 0.0
    off_delay_s: float = 0.0
    rtn_ack_required: bool = False
    latching: bool = False
    inhibit_by_alarm_ids: tuple[str, ...] = ()
    notification_policy_id: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "inhibit_by_alarm_ids", tuple(self.inhibit_by_alarm_ids))
        if self.latching and self.rtn_ack_required:
            raise ValueError("latching and rtn_ack_required cannot both be enabled")

    def message_for_locale(self, locale: str) -> str:
        if locale == "fi" and self.message_fi.strip():
            return self.message_fi
        return self.message


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: IssueSeverity
    code: str
    message: str
    object_type: str
    object_id: str
    field: str | None = None


@dataclass(frozen=True, slots=True)
class CompiledConfig:
    schema_version: str
    source_hash: str
    tags: tuple[TagDefinition, ...]
    alarms: tuple[AlarmDefinition, ...]
    notification_policies: tuple[NotificationPolicyDefinition, ...] = ()


@dataclass(frozen=True, slots=True)
class CompileResult:
    issues: tuple[ValidationIssue, ...]
    compiled: CompiledConfig | None

    @property
    def ok(self) -> bool:
        return self.compiled is not None and not any(
            issue.severity == IssueSeverity.ERROR for issue in self.issues
        )
