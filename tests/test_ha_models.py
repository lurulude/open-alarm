from datetime import UTC, datetime, timedelta

from open_alarm.backend.ha.models import (
    EntityQuality,
    apply_stale_quality,
    latest_state,
    normalize_entity_state,
)


def test_quality_states_are_explicit() -> None:
    now = datetime(2026, 8, 30, 10, tzinfo=UTC)

    good = normalize_entity_state("sensor.temp", {"state": "21.5"}, observed_at=now)
    unavailable = normalize_entity_state(
        "sensor.temp", {"state": "unavailable"}, observed_at=now
    )
    unknown = normalize_entity_state("sensor.temp", {"state": "unknown"}, observed_at=now)
    missing = normalize_entity_state("sensor.temp", None, observed_at=now)

    assert good.quality == EntityQuality.GOOD
    assert unavailable.quality == EntityQuality.UNAVAILABLE
    assert unknown.quality == EntityQuality.UNKNOWN
    assert missing.quality == EntityQuality.MISSING
    assert missing.usable is False


def test_stale_quality_does_not_overwrite_bad_quality() -> None:
    updated = datetime(2026, 8, 30, 9, tzinfo=UTC)
    now = updated + timedelta(seconds=61)
    good = normalize_entity_state(
        "sensor.temp",
        {"state": "21.5", "last_updated": updated.isoformat()},
        observed_at=updated,
    )
    unavailable = normalize_entity_state(
        "sensor.temp",
        {"state": "unavailable", "last_updated": updated.isoformat()},
        observed_at=updated,
    )

    assert apply_stale_quality(good, stale_after_s=60, now=now).quality == EntityQuality.STALE
    assert (
        apply_stale_quality(unavailable, stale_after_s=60, now=now).quality
        == EntityQuality.UNAVAILABLE
    )


def test_latest_state_prefers_newer_source_timestamp() -> None:
    old_time = datetime(2026, 8, 30, 9, tzinfo=UTC)
    new_time = old_time + timedelta(seconds=2)
    current = normalize_entity_state(
        "sensor.temp",
        {"state": "20", "last_updated": old_time.isoformat()},
        observed_at=old_time,
    )
    candidate = normalize_entity_state(
        "sensor.temp",
        None,
        observed_at=new_time,
        source_timestamp=new_time,
    )

    assert latest_state(current, candidate) is candidate
