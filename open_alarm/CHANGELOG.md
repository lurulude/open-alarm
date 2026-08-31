# Changelog

All notable Open Alarm release changes are documented here.

Open Alarm is currently in Beta. Beta releases may change engineering fields, database schema and UI behavior. Release notes will call out changes that require backup, migration or operator action.

## 0.1.0-beta.1 — 2026-08-31

First public Beta of Open Alarm.

### Alarm engine

- SCADA-style alarm lifecycle with `NORMAL`, `PENDING_ON`, `ACTIVE_UNACK`, `ACTIVE_ACK`, `PENDING_OFF` and `RTN_UNACK`.
- Analog HiHi/Hi/Lo/LoLo conditions with hysteresis and ON/OFF delays.
- Digital `EQUALS` / `NOT_EQUALS` alarms with raw-state debounce and ON/OFF delays.
- Device/quality alarms for unavailable, unknown, missing, stale and bad-quality sources.
- Restart-safe pending and debounce deadlines.
- Bad source quality retains the existing process-alarm lifecycle instead of falsely clearing it.
- Built-in system alarms for Home Assistant connection loss, runtime configuration load failure, notification worker failure and notification delivery failure.

### Operator controls

- Single and acknowledge-all workflows.
- Alarm reset for applicable alarm policies.
- Timed shelving and unshelving.
- Engineer suppression/unsuppression.
- Engineer out-of-service / return-to-service.
- Optional operator reason text with audited authenticated user identity.
- Built-in system alarms protected from shelving, suppression and out-of-service controls.

### Engineering

- One-table engineering workflow: **Save → Review changes → Activate**.
- Numeric row Alarm IDs assigned automatically.
- One analog row expands configured limits into stable internal runtime alarms.
- Searchable Home Assistant entity picker with friendly name, current value/unit, device and integration metadata where available.
- Manual Home Assistant entity ID entry retained as fallback.
- Atomic draft replacement with optimistic edit-conflict protection.
- Internal compiler validation and immutable reviewed revisions.
- Review validates entity-ID syntax without performing a full Home Assistant state download.
- Active process alarms no longer block configuration activation; compatible live state migrates while removed/incompatible engineered runtime state is reset and reevaluated.

### Alarm browser and history

- Active, unacknowledged, returned-unacknowledged, shelved, inhibited, suppressed and out-of-service browser views.
- Friendly Home Assistant source name and unit displayed for current alarm state when available.
- Localized operator alarm-condition labels instead of generated IDs such as `A2_LOLO` as the primary alarm text.
- New history events retain source friendly-name/unit metadata.
- History resolves known Open Alarm users to display names instead of showing internal user IDs.
- English and Finnish operator localization parity.

### Notifications

- Named Engineering notification groups with configurable title, multiple Home Assistant `notify.*` entity recipients and optional delay.
- Persistent notification outbox with retry and stale delayed-notification revalidation.
- Notification title contains only the configured group title; alarm state/priority are not appended to the title.
- Operator-visible notification text prefers configured Message, then Home Assistant friendly name, plus localized alarm condition and value/unit/state.
- Boolean/digital presentation uses operator-readable state text instead of raw Python `True`/`False`.
- “Other alarms” context contains only alarms that still require acknowledgement.
- Internal generated alarm IDs are hidden from primary phone-notification text.
- Generic notification-group delivery uses Home Assistant `notify.send_message` title/message only. Mobile-specific deep links/actionable ACK buttons are not part of the supported Beta.1 group path.

### Home Assistant integration

- Filtered `subscribe_entities` runtime monitoring for configured Home Assistant sources.
- Home Assistant connection/system quality handling.
- Ingress-only UI/API with a minimal `/healthz` watchdog exception.
- Home Assistant administrator verification is fail-closed.
- Home Assistant sidebar panel remains admin-only for Beta.1.
- Open Alarm publishes `sensor.open_alarm_unacknowledged` and `binary_sensor.open_alarm_attention` through the Supervisor/Core API.
- Both attention states are refreshed while Open Alarm is running and marked unavailable on a clean App stop.

### Optional global alarm indicator

- Added `open_alarm_indicator.js` as an opt-in Home Assistant frontend module.
- Indicator is hidden when no acknowledgement is required.
- Shows red `⚠ N` for unacknowledged alarms.
- Shows amber `⚠ ?` when the published Open Alarm state is unavailable after a clean stop.
- Selecting the indicator opens Open Alarm.
- The App does not request `/config` write permission to install the optional module automatically.

### Persistence and packaging

- SQLite/WAL persistence for configuration revisions, runtime alarm state, pending deadlines, control state, history, audit, users/locales and notification outbox.
- Automatic schema migrations on startup.
- Home Assistant cold-backup declaration.
- Packaged App support for `aarch64` and `amd64`.
- Source-built App repository installation for Beta.1; prebuilt registry images are not required.
- Apache License 2.0.

### CI and release gate

- Ruff + pytest backend gate.
- Production React frontend build.
- JavaScript syntax check for the optional corner indicator.
- Real packaged-App Docker build.
- Persistent boot/restart smoke test with SQLite integrity/migration verification.

### Known Beta limitations

- App panel access is currently limited to Home Assistant administrators.
- The repository builds the App container locally rather than distributing prebuilt multi-architecture images.
- Generic notification groups do not provide mobile-app-specific tap-to-open/action buttons.
- The optional global corner indicator depends on Home Assistant frontend DOM/navigation behavior and may require adjustment after a major frontend redesign.
- Runtime compatibility fallback can use one full `get_states` bootstrap if filtered Home Assistant entity subscription is unavailable.
- Historical events created before source friendly-name/unit metadata was recorded cannot be retroactively enriched with missing metadata.
