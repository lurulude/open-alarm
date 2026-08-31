from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any


class EntityQuality(str, Enum):
    GOOD = "GOOD"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    MISSING = "MISSING"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class HAEntityState:
    entity_id: str
    state: str | None
    attributes: Mapping[str, Any]
    quality: EntityQuality
    last_changed: datetime | None
    last_updated: datetime | None
    observed_at: datetime

    @property
    def usable(self) -> bool:
        return self.quality == EntityQuality.GOOD


def parse_ha_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_entity_state(
    entity_id: str,
    payload: Mapping[str, Any] | None,
    *,
    observed_at: datetime | None = None,
    source_timestamp: datetime | None = None,
) -> HAEntityState:
    observed = observed_at or datetime.now(UTC)

    if payload is None:
        return HAEntityState(
            entity_id=entity_id,
            state=None,
            attributes={},
            quality=EntityQuality.MISSING,
            last_changed=None,
            last_updated=source_timestamp,
            observed_at=observed,
        )

    raw_state = payload.get("state")
    state = None if raw_state is None else str(raw_state)
    state_lower = state.lower() if state is not None else ""

    if state_lower == "unavailable":
        quality = EntityQuality.UNAVAILABLE
    elif state_lower == "unknown" or state is None:
        quality = EntityQuality.UNKNOWN
    else:
        quality = EntityQuality.GOOD

    attributes_raw = payload.get("attributes")
    attributes = dict(attributes_raw) if isinstance(attributes_raw, Mapping) else {}

    return HAEntityState(
        entity_id=entity_id,
        state=state,
        attributes=attributes,
        quality=quality,
        last_changed=parse_ha_datetime(payload.get("last_changed")),
        last_updated=parse_ha_datetime(payload.get("last_updated")) or source_timestamp,
        observed_at=observed,
    )


def apply_stale_quality(
    state: HAEntityState,
    *,
    stale_after_s: float | None,
    now: datetime | None = None,
) -> HAEntityState:
    if stale_after_s is None:
        return state
    if stale_after_s < 0:
        raise ValueError("stale_after_s must be >= 0")
    if state.quality != EntityQuality.GOOD:
        return state

    reference = state.last_updated or state.observed_at
    current = now or datetime.now(UTC)
    if current - reference >= timedelta(seconds=stale_after_s):
        return replace(state, quality=EntityQuality.STALE)
    return state


def latest_state(current: HAEntityState | None, candidate: HAEntityState) -> HAEntityState:
    if current is None:
        return candidate

    current_time = current.last_updated or current.observed_at
    candidate_time = candidate.last_updated or candidate.observed_at
    return candidate if candidate_time >= current_time else current
