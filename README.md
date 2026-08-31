# Open Alarm

**SCADA-style alarm management for Home Assistant.**

Open Alarm is a Home Assistant OS App that adds a dedicated alarm engine for process-style monitoring. Instead of building one helper or automation for every condition, alarms are engineered in one table and handled through a persistent lifecycle with acknowledgement, delays, hysteresis, history and operator controls.

Current release: **0.1.0-beta.2**.

> [!IMPORTANT]
> Open Alarm is Beta software, not a certified safety system. Do not use it as the sole protective layer for life-safety, fire, medical, machinery protection or other safety-critical functions.

## Why Open Alarm

Home Assistant is excellent at automation and visualization, but industrial alarm handling needs more than a threshold changing a color. Open Alarm provides explicit alarm states, acknowledgement, return handling, restart-safe timers, operator history and controlled engineering changes.

The design intentionally stays small: one App, one SQLite database, one engineering alarm table, one runtime engine and one Home Assistant connection path.

## Highlights

- Analog **HiHi / Hi / Lo / LoLo** alarms with shared hysteresis and ON/OFF delays.
- Digital alarms with `EQUALS` / `NOT_EQUALS`, debounce and ON/OFF delays.
- Device/quality alarms for unavailable, unknown, missing, stale and bad-quality sources.
- Lifecycle states: `NORMAL`, `PENDING_ON`, `ACTIVE_UNACK`, `ACTIVE_ACK`, `PENDING_OFF`, `RTN_UNACK`.
- Acknowledge one alarm or all unacknowledged alarms.
- Shelving, suppression and out-of-service controls with audit history.
- Restart-safe pending and debounce deadlines.
- Bad quality never falsely clears an existing process alarm.
- Engineering workflow: **Save → Review changes → Activate**.
- Searchable Home Assistant entity picker with friendly name, device information, current value and unit.
- Notification groups targeting Home Assistant `notify.*` entities.
- Persistent notification outbox with delay, retry and stale-notification revalidation.
- Alarm browser and history with friendly Home Assistant source names and units.
- English and Finnish operator UI/backend localization.
- Native Home Assistant alarm-attention states plus an optional always-visible browser corner indicator.
- Responsive phone/tablet layout for alarm, history, Engineering and Admin views.
- SQLite/WAL persistence and Home Assistant cold-backup integration.
- Release-time Python/npm license audit plus bundled third-party notices.

## Supported environment

Open Alarm `0.1.0-beta.2` is packaged as a Home Assistant App for:

- `aarch64`
- `amd64`

The App uses Home Assistant Ingress and the Supervisor/Core API. The current Home Assistant sidebar panel is intentionally **admin-only** (`panel_admin: true`). Open Alarm still maintains Viewer, Operator, Engineer and Admin roles internally for authorization and audit, but Beta UI access through the standard sidebar currently requires a Home Assistant administrator.

## Install and update

1. In Home Assistant, open **Settings → Apps → App store**.
2. Add this App repository:

   `https://github.com/lurulude/open-alarm`

3. Refresh the App store.
4. Install **Open Alarm**.
5. Start the App.
6. Open **Open Alarm** from the Home Assistant sidebar.

When a newer `version` is published in `open_alarm/config.yaml`, Home Assistant Supervisor can offer the App update through the normal App UI. Beta.2 is a normal forward update from Beta.1.

Home Assistant authenticates Ingress users and Supervisor supplies their `X-Remote-User-*` identity headers to Open Alarm. Because the Open Alarm panel is admin-only, the first authenticated ingress user to open Open Alarm becomes the first Open Alarm Admin. Later users are added with least privilege and can be assigned an Open Alarm role by an Open Alarm Admin.

The Beta repository currently builds the App image from source on the Home Assistant machine. Prebuilt registry images are not required for Beta. The first install, update or rebuild can therefore take longer on small hardware.

## First alarm in five minutes

Open **Engineering** and add a row. Open Alarm assigns the next numeric Alarm ID automatically.

For an analog source:

1. Select or enter a Home Assistant entity such as `sensor.tank_temperature`.
2. Choose **Analog**.
3. Enter at least one limit: HiHi, Hi, Lo or LoLo.
4. When multiple limits are used, configured values must descend: `HiHi > Hi > Lo > LoLo`.
5. Configure hysteresis, ON delay, OFF delay, priority and an optional operator message.
6. Optionally select a notification group.
7. Select **Save all**.
8. Select **Review changes** and resolve blocking validation errors.
9. Inspect the preview and select **Activate revision**.

One engineering row represents one Home Assistant source. Internally, row `1` may produce `T1` and alarms such as `A1_HIHI` or `A1_LOLO`, but those IDs are engine details. Operator views use friendly source names and localized condition labels.

## Alarm behavior

### Analog

High alarms activate at or above their setpoint and return below `setpoint - hysteresis`. Low alarms activate at or below their setpoint and return above `setpoint + hysteresis`.

ON and OFF delays require the condition to remain continuously qualified for the full delay. Reversing the condition cancels the pending transition.

### Digital

Digital alarms compare the Home Assistant state to the configured alarm value with `EQUALS` or `NOT_EQUALS`.

Digital debounce qualifies the raw source change first; ON/OFF delay then qualifies the alarm transition. For an `input_boolean`, operator presentation uses Home Assistant-style state text instead of exposing raw Python booleans.

### Device / quality

Device rows can alarm on `UNAVAILABLE`, `UNKNOWN`, `MISSING`, `STALE` or `BAD_QUALITY`. Source quality faults are explicit and do not silently normalize an already-active process alarm.

## Operator workflow

The alarm browser separates active, unacknowledged, returned-unacknowledged, shelved, inhibited, suppressed and out-of-service alarms. The **Unacknowledged** view is also the canonical count used by phone-notification context and the Home Assistant attention states.

Operators can acknowledge and shelve alarms. Engineers can additionally suppress engineered alarms and take them out of service. A reason is optional; when supplied it is stored in audit history. Built-in system alarms cannot be hidden using shelving, suppression or out-of-service controls.

## Engineering and activation

Engineering is intentionally one user-facing table rather than separate Tag, Equipment and Compile editors.

`EDIT → SAVE → REVIEW → ACTIVATE`

**Save** atomically replaces the working draft's generated tags, alarms and notification policies. **Review** validates the saved configuration and creates or reuses an immutable candidate revision. Review validates Home Assistant entity-ID syntax; it does not require a full live Home Assistant state lookup. **Activate** switches the active revision and reloads runtime monitoring.

Active alarms do not block configuration activation. If a stable alarm definition remains compatible, live state is migrated. Removed or incompatible engineered alarm state is reset and the new definition is evaluated by the runtime. Existing history is retained.

## Notifications

Notification groups are configured once in Engineering and selected by alarm rows. A group contains:

- an operator-facing group name;
- a configurable phone-notification title;
- one or more Home Assistant `notify.*` entity recipients;
- an optional notification delay.

The current group transport uses Home Assistant's generic `notify.send_message` path and sends **title + message**. Operator-visible notification text uses the configured Message when present, otherwise the Home Assistant friendly name, localized alarm condition and current value/unit. The compact **other alarms** section contains only alarms that still require acknowledgement.

Because the generic group transport intentionally strips integration-specific mobile data, the current group notification does **not** provide a tap-to-open Open Alarm deep link or mobile actionable ACK buttons. Those would require a separate mobile-app-specific transport and are not part of Beta.2.

## Home Assistant attention states

Open Alarm automatically publishes:

- `sensor.open_alarm_unacknowledged` — current number of alarms requiring acknowledgement;
- `binary_sensor.open_alarm_attention` — `on` when the count is greater than zero, `off` otherwise, with `device_class: problem`.

These states can be used in Home Assistant dashboards and automations. Shelved, suppressed, inhibited, out-of-service and already acknowledged alarms are excluded from the count.

On a clean App stop, Open Alarm marks these states unavailable. The App republishes them while running and periodically refreshes them.

## Optional always-visible corner indicator

`open_alarm/open_alarm_indicator.js` is an optional Home Assistant frontend module that displays a fixed indicator in the top-right corner of the Home Assistant frontend:

- no unacknowledged alarms → hidden;
- one or more → red `⚠ N`;
- missing/unavailable Open Alarm state → amber `⚠ ?`;
- click → opens the registered Open Alarm panel.

The overlay is **not installed automatically**. This keeps the App from requesting write access to `/config` just for frontend decoration. It is useful in desktop browsers, but Companion-app WebView behavior is best effort because Home Assistant does not provide a supported global-overlay API for Apps. See [the App guide](open_alarm/DOCS.md#optional-always-visible-corner-indicator) for setup.

## Persistence and backups

Runtime data lives in `/data/open_alarm.db` inside the App data directory. SQLite runs in WAL mode. Open Alarm persists engineering revisions, active configuration, alarm-relevant runtime state, pending deadlines, history, audit, user roles/locales and the notification outbox.

The App declares Home Assistant `backup: cold`; normal Home Assistant App backups therefore capture the stopped App data consistently. Database schema migrations run automatically at startup.

## Security model

- UI/API traffic is available through Home Assistant Ingress only; the production app rejects non-watchdog requests that do not come from Supervisor's ingress proxy.
- `/healthz` is the minimal direct watchdog endpoint.
- Home Assistant authenticates the ingress session and Supervisor supplies the authenticated user's `X-Remote-User-*` headers.
- The Home Assistant panel is admin-only; Open Alarm does not request broad Supervisor administrator privileges merely to re-check the same ingress user.
- Open Alarm applies its own Viewer / Operator / Engineer / Admin authorization to API actions.
- Configuration, control and acknowledgement actions are associated with the authenticated user for audit.
- The optional corner indicator does not grant the App extra filesystem permissions.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and supported-version policy.

## Beta limitations

- Beta may still change database schema, engineering fields and UI behavior.
- Only `aarch64` and `amd64` App architectures are packaged today.
- App installation currently builds the container locally instead of downloading a prebuilt registry image.
- The Home Assistant panel is admin-only in Beta.2.
- Generic notification groups do not provide mobile-app-specific deep links/actions.
- The optional fixed corner overlay depends on Home Assistant frontend behavior and is not guaranteed inside Companion-app WebViews.
- Existing history created before friendly-name/unit metadata was stored cannot be retroactively enriched with information that was never recorded.

## Documentation

- [App guide](open_alarm/DOCS.md) — install, configuration, operation, notifications, indicator, backup, troubleshooting and verification.
- [Architecture](ARCHITECTURE.md) — runtime design, lifecycle, persistence, Home Assistant interfaces and failure behavior.
- [Changelog](open_alarm/CHANGELOG.md) — release notes.
- [Third-party notices](open_alarm/THIRD_PARTY_NOTICES.md) — audited package/container licenses and attribution.
- [Source provenance](PROVENANCE.md) — project/AI-assisted source provenance policy and limits.
- [Notice](NOTICE) — release attribution notice.
- [Contributing](CONTRIBUTING.md) — development setup and contribution expectations.
- [Security](SECURITY.md) — security policy and vulnerability reporting.
- [Releasing](RELEASING.md) — maintainer release/version/CI checklist.

## Development

Backend:

```bash
python -m pip install -r open_alarm/requirements.txt 'httpx2>=2.12,<3' pytest ruff
python open_alarm/license_audit.py fastapi uvicorn pydantic websockets httpx2 pytest ruff
ruff check open_alarm tests
pytest -q
```

Frontend and optional indicator syntax:

```bash
cd open_alarm/frontend
npm install --no-audit --no-fund
node license-audit.mjs node_modules
npm run build
node --check ../open_alarm_indicator.js
```

Packaged App:

```bash
docker build \
  --build-arg BUILD_VERSION=0.1.0-beta.2 \
  --build-arg BUILD_ARCH=amd64 \
  -t open-alarm ./open_alarm
```

CI runs dependency-license audits, Python lint/tests, the frontend build, JavaScript checks and a real packaged-App boot/persistent-restart smoke test. The packaged image contains its Open Alarm license, third-party notices, runtime Python license files and the actual Python/npm package-license inventories used by the build.

## License and provenance

Open Alarm project source is licensed under the [Apache License 2.0](LICENSE). Third-party components are **not** relicensed as Open Alarm; their licenses and copyrights are retained in [THIRD_PARTY_NOTICES.md](open_alarm/THIRD_PARTY_NOTICES.md) and the packaged App license inventory.

Open Alarm has been developed with generative-AI assistance. A model cannot provide a reliable per-line training-data source map, so the project does not invent one. The source-provenance policy, copied-code rules and limits of that record are documented in [PROVENANCE.md](PROVENANCE.md).

The release audit rejects proprietary/restricted/unknown-license Python and npm dependencies. This compliance work reduces licensing risk but is not a legal guarantee or substitute for professional legal advice where contractual assurance is required.
