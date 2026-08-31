from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from ..config.models import TagDefinition
from ..ha.models import HAEntityState, apply_stale_quality, normalize_entity_state


@dataclass(frozen=True, slots=True)
class TagStateChange:
    tag_id: str
    previous: HAEntityState | None
    current: HAEntityState


class TagManager:
    def __init__(self, tags: tuple[TagDefinition, ...]) -> None:
        self._tags = {tag.tag_id: tag for tag in tags if tag.enabled}
        entity_map: dict[str, list[str]] = defaultdict(list)
        for tag in self._tags.values():
            entity_map[tag.entity_id].append(tag.tag_id)
        self._entity_to_tags = {key: tuple(value) for key, value in entity_map.items()}
        self._states: dict[str, HAEntityState] = {}

    @property
    def monitored_entity_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._entity_to_tags))

    def get(self, tag_id: str) -> HAEntityState | None:
        return self._states.get(tag_id)

    def update_entity(
        self,
        state: HAEntityState,
        *,
        now: datetime | None = None,
    ) -> tuple[TagStateChange, ...]:
        current_time = now or datetime.now(UTC)
        changes: list[TagStateChange] = []

        for tag_id in self._entity_to_tags.get(state.entity_id, ()):
            tag = self._tags[tag_id]
            normalized = apply_stale_quality(
                state,
                stale_after_s=tag.stale_after_s,
                now=current_time,
            )
            previous = self._states.get(tag_id)
            self._states[tag_id] = normalized
            if previous != normalized:
                changes.append(TagStateChange(tag_id, previous, normalized))

        return tuple(changes)

    def initialize_missing(self, *, now: datetime | None = None) -> tuple[TagStateChange, ...]:
        current_time = now or datetime.now(UTC)
        changes: list[TagStateChange] = []
        for tag_id, tag in self._tags.items():
            if tag_id in self._states:
                continue
            state = normalize_entity_state(tag.entity_id, None, observed_at=current_time)
            self._states[tag_id] = state
            changes.append(TagStateChange(tag_id, None, state))
        return tuple(changes)

    def refresh_stale(self, *, now: datetime | None = None) -> tuple[TagStateChange, ...]:
        current_time = now or datetime.now(UTC)
        changes: list[TagStateChange] = []

        for tag_id, previous in tuple(self._states.items()):
            tag = self._tags[tag_id]
            current = apply_stale_quality(
                previous,
                stale_after_s=tag.stale_after_s,
                now=current_time,
            )
            if current != previous:
                self._states[tag_id] = current
                changes.append(TagStateChange(tag_id, previous, current))

        return tuple(changes)
