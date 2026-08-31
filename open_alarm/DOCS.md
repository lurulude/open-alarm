# Open Alarm App Guide

Open Alarm is a Home Assistant OS App for SCADA-style alarm management. This guide covers installation, configuration, alarm behavior, notifications, Home Assistant attention states, the optional corner indicator, mobile UI, persistence, backup/recovery, licensing and troubleshooting for **0.1.0-beta.2**.

> [!WARNING]
> Open Alarm is Beta software and is not a certified safety system. It must not be the sole protective layer for life-safety, fire, medical, machinery-protection or other safety-critical functions.

## 1. Requirements

Open Alarm Beta.2 supports Home Assistant App installations on:

- `aarch64`
- `amd64`

The App uses Home Assistant Ingress and Supervisor/Core API access, declares `homeassistant_api: true`, boots automatically, has a Supervisor watchdog and participates in Home Assistant cold backups.

The current sidebar panel is `panel_admin: true`, so standard sidebar access requires a Home Assistant administrator. Open Alarm still maintains Viewer, Operator, Engineer and Admin roles internally for authorization and audit.

## 2. Install and update

Add the App repository in **Settings → Apps → App store**:

`https://github.com/lurulude/open-alarm`

Refresh the App store, install **Open Alarm**, start it and open it from the sidebar.

The repository currently builds the App container from source on the Home Assistant machine. Installation/update therefore needs network access for the referenced base images and package dependencies and can take several minutes on smaller hardware.

Home Assistant detects Open Alarm updates from the `version` in `config.yaml`. Beta.2 is a normal forward update from Beta.1. No database migration or Engineering configuration change is required for the Beta.1 → Beta.2 update.

### First administrator

Ingress identity is verified against Home Assistant. The first verified Home Assistant administrator to open Open Alarm becomes the first Open Alarm **Admin**. Later verified Home Assistant administrators are created with least privilege and can be assigned an Open Alarm role by an Open Alarm Admin.

Roles:

- **Viewer** — read alarms, history and runtime state.
- **Operator** — Viewer plus acknowledgement, reset and shelving.
- **Engineer** — Operator plus Engineering, suppression and out-of-service controls.
- **Admin** — Engineer plus revision activation and Open Alarm user-role administration.

## 3. First boot

A fresh install with no active alarm configuration is valid. Expected behavior:

- `/data/open_alarm.db` is created and migrations run;
- App/watchdog remain healthy;
- notification worker starts;
- runtime reports no active revision until one is activated;
- UI/API is accepted through Home Assistant Ingress;
- Home Assistant receives `sensor.open_alarm_unacknowledged` and `binary_sensor.open_alarm_attention`.

## 4. Engineering workflow

Open Alarm intentionally uses one engineering alarm table instead of separate Tag, Equipment, Template or Compile editors.

`EDIT → SAVE → REVIEW → ACTIVATE`

Each row represents one Home Assistant source and gets the next numeric Alarm ID automatically. Internally row `1` uses tag `T1`; an analog row can generate runtime alarms such as `A1_HIHI`, `A1_HI`, `A1_LO` and `A1_LOLO`. Those are stable engine identities, not primary operator text.

**Save all** atomically replaces the draft's generated source tags, alarms and notification policies. Optimistic draft timestamps prevent a stale browser session from silently overwriting a newer save.

**Review changes** validates the saved configuration and creates/reuses an immutable candidate revision. Review validates entity-ID syntax and configuration rules; it deliberately does not download every Home Assistant state to prove live existence.

**Activate revision** switches the active immutable revision and reloads runtime monitoring. Active alarms do not block activation. Compatible stable alarms migrate live runtime state; removed/incompatible engineered state is reset and reevaluated. Existing history remains.

## 5. Analog alarms

Configure one or more of **HiHi**, **Hi**, **Lo**, **LoLo**. Blank limits do not create runtime alarms. If multiple limits are configured:

`HiHi > Hi > Lo > LoLo`

Set shared hysteresis, ON delay, OFF delay, priority, optional Message and optional notification group.

High/High-high alarms are abnormal at `value >= setpoint` and return below `setpoint - hysteresis`. Low/Low-low alarms are abnormal at `value <= setpoint` and return above `setpoint + hysteresis`.

ON/OFF delays require continuous qualification; reversing before the deadline cancels the pending transition.

## 6. Digital alarms

Choose `EQUALS` or `NOT_EQUALS` and configure the state value that defines abnormal. Digital timing has two stages:

1. `debounce_on_s` / `debounce_off_s` qualify the raw source change;
2. `on_delay_s` / `off_delay_s` qualify the alarm transition.

A reversal cancels the current pending debounce/transition. Common boolean/input-boolean presentation uses readable on/off state text instead of raw Python booleans.

## 7. Device / quality alarms

Device rows can alarm on:

- `UNAVAILABLE`
- `UNKNOWN`
- `MISSING`
- `STALE`
- `BAD_QUALITY`

Device/quality alarms use ON/OFF delays rather than digital debounce. A source-quality fault does **not** falsely normalize an already-active process alarm.

## 8. Home Assistant source picker

The Engineering picker uses Home Assistant registry/device data plus a small filtered current-state preview. It can show entity ID, friendly name, value/unit, device name, manufacturer/model, platform and device class when available.

Manual entity IDs remain accepted. Runtime stores last-known source `friendly_name` and `unit_of_measurement` with alarm-relevant state so active alarms/history can show useful text without querying Home Assistant on every browser refresh.

## 9. Alarm lifecycle

Primary lifecycle states:

- `NORMAL`
- `PENDING_ON`
- `ACTIVE_UNACK`
- `ACTIVE_ACK`
- `PENDING_OFF`
- `RTN_UNACK`

Shelving, suppression, inhibition, out-of-service and latching are separate control state.

The canonical **Unacknowledged** count includes `ACTIVE_UNACK`, `RTN_UNACK`, and `PENDING_OFF` when its origin was `ACTIVE_UNACK`. It excludes shelved, suppressed, inhibited, out-of-service and already acknowledged alarms. The same definition drives the browser view, notification context and Home Assistant attention states.

## 10. Operator controls

Operators can acknowledge, reset where policy/state permits, and shelve for a bounded duration. Engineers can additionally suppress/unsuppress engineered alarms and take them out of service/return them to service.

A reason is optional for current shelving/suppression/out-of-service actions. When supplied it is stored with the audited authenticated user and event time.

Built-in system alarms can be acknowledged but cannot be shelved, suppressed or taken out of service.

## 11. Built-in system alarms

Open Alarm includes persisted system alarms for failures in the alarm-management path itself, including:

- Home Assistant connection lost — P1 with a short ON delay;
- active configuration could not be loaded — P1;
- notification delivery worker stopped — P1;
- notification delivery failed — P2.

## 12. Restart safety and source quality

Alarm ON/OFF and digital-debounce deadlines are persisted when they matter. After restart, Open Alarm reloads persisted state, reconciles it with the current source and continues/completes/cancels the original deadline rather than blindly restarting timers from zero.

`unavailable`, `unknown`, missing/stale sources and Home Assistant connection loss are explicit bad quality. Bad quality does not clear an existing process alarm to normal.

## 13. Notification groups

Engineering notification groups contain:

- operator-facing group name;
- notification title;
- one or more Home Assistant `notify.*` entity recipients;
- optional delay.

Activation notification text prefers the configured Message, otherwise the Home Assistant friendly name, then localized alarm condition and current value/unit/state. Up to three other alarms that still require acknowledgement may be shown. Generated IDs such as `A2_LOLO` are not the primary phone text.

Alarm transition and notification-outbox enqueue are committed together. Delayed notifications are revalidated before send. Failed deliveries remain in the persistent outbox for retry and persistent failure raises a built-in system alarm.

### Mobile-action limitation

The current group transport uses Home Assistant `notify.send_message` and sends **title + message**. Integration-specific mobile `data` is not forwarded, so Beta.2 generic groups do not provide tap-to-open deep links or actionable ACK buttons.

## 14. Home Assistant attention states

Open Alarm publishes:

- `sensor.open_alarm_unacknowledged` — integer canonical unacknowledged count;
- `binary_sensor.open_alarm_attention` — `on` when count > 0, `off` at zero, `unavailable` on a clean App stop; `device_class: problem`.

These native states are the supported source for Home Assistant dashboards/automations and do not require a frontend extension.

## 15. Optional corner indicator

`open_alarm/open_alarm_indicator.js` is an optional user-managed Home Assistant frontend module.

Behavior:

- zero unacknowledged alarms → hidden;
- one or more → red `⚠ N`;
- missing/unavailable Open Alarm state → amber `⚠ ?`;
- selecting it navigates to the registered Open Alarm panel.

Copy it to `/config/www/open_alarm_indicator.js` and configure:

```yaml
frontend:
  extra_module_url:
    - /local/open_alarm_indicator.js?v=2
```

Restart Home Assistant Core and hard-refresh/reopen the frontend. The module discovers the actual registered App panel path, so it supports Local App and repository-style panel IDs.

The App deliberately does not request `/config` write permission just to install this decoration. The overlay works in normal browser frontends; **Companion-app WebView behavior is best effort**, because Home Assistant does not provide Apps a supported global-overlay API. The native attention states remain the source of truth.

## 16. Responsive/mobile UI

Beta.2 removes the previous fixed desktop-width canvas. On phone-sized screens:

- navigation scrolls within its bar;
- alarm/history tables scroll inside the content area;
- Engineering stacks vertically and keeps wide engineering grids locally scrollable;
- filters and control actions reflow;
- Admin/notification views stay within the viewport.

If a narrow screen still forces the whole Open Alarm page wider than the Home Assistant viewport, report the page, device/viewport width and screenshot.

## 17. History and localization

History records lifecycle and operator-control events. New events retain friendly-name/unit metadata when available and resolve known Open Alarm users to display names. Internal IDs remain stored for diagnostics/search.

English and Finnish UI/backend catalogs are kept in parity. Home Assistant-provided friendly names are displayed as supplied; Open Alarm does not translate the user's entity names.

## 18. Persistence

Primary database:

`/data/open_alarm.db`

SQLite/WAL stores Engineering drafts/generated objects, immutable revisions/active pointer, runtime alarm state, pending deadlines, source display metadata, controls, history/audit, Open Alarm users/locales, notification outbox/retry state and runtime connectivity events.

Schema migrations run automatically at startup and are recorded in the schema-migration table.

## 19. Backup and restore

The App declares `backup: cold`. Use Home Assistant's normal App backup/restore flow so Open Alarm is stopped while SQLite data is captured. Do not copy an active SQLite/WAL database as the normal backup method.

Before a Beta update that changes persistence/configuration behavior, take and verify a Home Assistant backup, then update, start the App, verify runtime/active revision and test a representative alarm path.

## 20. License, third-party software and provenance

Open Alarm **project source** is licensed under the Apache License 2.0. The complete license is included both at repository root and in `open_alarm/LICENSE` so it is available inside the App build context.

Third-party software retains its own licenses and copyrights. See:

- `../NOTICE`
- `../PROVENANCE.md`
- `THIRD_PARTY_NOTICES.md`

Beta.2 audits the actual installed Python/npm dependency graphs during CI and App build. Unknown/unreviewed license metadata fails the build. The policy rejects proprietary, commercial-only, noncommercial-only, field-of-use-restricted and source-available-but-restricted Python/npm dependencies.

The built App image contains `/app/licenses/` with:

- Open Alarm Apache license;
- third-party notices;
- actual Python/npm package/version/license inventories;
- available Python runtime package license files;
- React, React DOM and Scheduler MIT license files because their code is incorporated into the production browser bundle.

Open Alarm has been developed with generative-AI assistance. A model does not provide a reliable searchable per-line map back to training sources, so the project does not invent one. `PROVENANCE.md` documents that limitation and requires distinctive code with uncertain third-party provenance to be attributed/licensed correctly or replaced with a clean implementation.

Home Assistant public API/configuration identifiers are used for interoperability. Home Assistant developer-documentation prose/examples are separately licensed and are not intentionally redistributed as Open Alarm documentation/source.

This compliance work reduces licensing risk; it is not legal advice or a guarantee that no intellectual-property claim can ever be made.

## 21. Update verification

After an App update:

1. read `CHANGELOG.md`;
2. refresh the App repository and install the offered update;
3. confirm the App starts and runtime reports the expected active revision/source count;
4. verify `sensor.open_alarm_unacknowledged`;
5. test one representative analog or digital alarm;
6. if the optional indicator file changed, replace the `/config/www/` copy and cache-bust/reload it.

The source-built App can take longer to update than a prebuilt registry image.

## 22. Troubleshooting

### App does not appear in the store

Confirm repository URL, refresh the store and verify architecture is `aarch64` or `amd64`.

### Build fails during dependency license audit

Do not bypass the check. Read the build log for the package/license that failed. If an upstream package has changed license or stopped declaring one, the release must explicitly review, pin, replace or remove it before proceeding.

### App starts with no active configuration

Normal on a fresh install. Create, Save, Review and Activate an Engineering revision.

### Entity picker does not load

Manual entity IDs remain valid. The picker depends on Home Assistant registry/device calls; Review should not require a full state download.

### Raw entity ID or missing unit appears

Friendly name/unit come from Home Assistant state metadata. The entity ID/no-unit fallback is correct when metadata is absent. Old history cannot be retroactively enriched with data that was never stored.

### Notifications do not deep-link to Open Alarm

Expected for the current generic `notify.send_message` group transport.

### Attention count looks wrong

Compare with the **Unacknowledged** browser tab and remember hidden/acknowledged alarms are intentionally excluded.

### Corner indicator missing

Verify `/config/www/open_alarm_indicator.js`, `frontend.extra_module_url`, restart Core, hard-refresh, and check the native attention states. Amber `⚠ ?` means the module loaded but Open Alarm state is missing/unavailable. Companion WebView absence alone does not mean the browser module is broken.

### Home Assistant unavailable

Open Alarm retains the current process lifecycle instead of falsely clearing it and raises its Home Assistant connection-loss system alarm after the configured short delay.

## 23. Verification checklist

Before relying on a Beta build, verify what matters to the installation:

- analog threshold/hysteresis/ON/OFF behavior;
- digital debounce and delays;
- restart during a pending timer;
- bad quality does not falsely clear an active alarm;
- acknowledgement survives restart where applicable;
- shelving, suppression and out-of-service controls;
- activation while an alarm is active;
- notification delivery/delay cancellation;
- Home Assistant attention count;
- phone-sized UI if used;
- optional corner indicator in the intended browser if enabled;
- persistent `/data/open_alarm.db`/active revision after restart.

## 24. Local development install

For a Local App, copy the complete `open_alarm` directory to `/addons/open_alarm`, refresh **Local Apps** and install/rebuild. `config.yaml` currently has no `image` key, so Local Apps build from source.

Backend development runs the Python license audit before lint/tests; frontend development runs the npm license audit before build. See repository `CONTRIBUTING.md` and `RELEASING.md`.

## 25. Reporting problems

For normal bugs, open a GitHub issue with Open Alarm version, Home Assistant version/architecture, affected area, relevant App logs and concise reproduction steps. Do not publish credentials, Supervisor tokens, private notification payloads or an unredacted database.

For security vulnerabilities, follow repository `SECURITY.md`.

For suspected unattributed/copyrighted source, identify the Open Alarm file and suspected upstream source so maintainers can establish the license/notice requirement or replace the implementation.

Source repository:

`https://github.com/lurulude/open-alarm`
