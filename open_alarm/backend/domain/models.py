from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AlarmLifecycle(str, Enum):
    NORMAL = "NORMAL"
    PENDING_ON = "PENDING_ON"
    ACTIVE_UNACK = "ACTIVE_UNACK"
    ACTIVE_ACK = "ACTIVE_ACK"
    PENDING_OFF = "PENDING_OFF"
    RTN_UNACK = "RTN_UNACK"


class AlarmEventType(str, Enum):
    PENDING_ON = "PENDING_ON"
    PENDING_CANCEL = "PENDING_CANCEL"
    ACTIVATE = "ACTIVATE"
    ACK = "ACK"
    PENDING_OFF = "PENDING_OFF"
    RETURN_CANCEL = "RETURN_CANCEL"
    RETURN = "RETURN"
    ACK_RETURN = "ACK_RETURN"
    REACTIVATE = "REACTIVATE"
    RESET = "RESET"
    SHELVE = "SHELVE"
    UNSHELVE = "UNSHELVE"
    SUPPRESS = "SUPPRESS"
    UNSUPPRESS = "UNSUPPRESS"
    INHIBIT = "INHIBIT"
    UNINHIBIT = "UNINHIBIT"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    IN_SERVICE = "IN_SERVICE"


class AnalogCondition(str, Enum):
    HIGH_HIGH = "HIGH_HIGH"
    HIGH = "HIGH"
    LOW = "LOW"
    LOW_LOW = "LOW_LOW"


@dataclass(frozen=True, slots=True)
class AlarmPolicy:
    on_delay_s: float = 0.0
    off_delay_s: float = 0.0
    rtn_ack_required: bool = False
    latching: bool = False

    def __post_init__(self) -> None:
        if self.on_delay_s < 0 or self.off_delay_s < 0:
            raise ValueError("alarm delays must be >= 0")
        if self.latching and self.rtn_ack_required:
            raise ValueError("latching and rtn_ack_required cannot both be enabled")


@dataclass(frozen=True, slots=True)
class AnalogRule:
    condition: AnalogCondition
    setpoint: float
    hysteresis: float
    on_delay_s: float = 0.0
    off_delay_s: float = 0.0

    def __post_init__(self) -> None:
        if self.hysteresis < 0:
            raise ValueError("hysteresis must be >= 0")
        if self.on_delay_s < 0 or self.off_delay_s < 0:
            raise ValueError("alarm delays must be >= 0")


@dataclass(frozen=True, slots=True)
class DigitalRule:
    alarm_value: str
    debounce_on_s: float = 0.0
    debounce_off_s: float = 0.0
    on_delay_s: float = 0.0
    off_delay_s: float = 0.0

    def __post_init__(self) -> None:
        values = (self.debounce_on_s, self.debounce_off_s, self.on_delay_s, self.off_delay_s)
        if any(value < 0 for value in values):
            raise ValueError("digital debounce and delay values must be >= 0")
