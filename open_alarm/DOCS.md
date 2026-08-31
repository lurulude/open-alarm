# Open Alarm App Guide

Open Alarm is a Home Assistant OS App for SCADA-style alarm management. This guide covers installation, first configuration, alarm behavior, notifications, Home Assistant attention states, the optional corner indicator, backup/recovery and troubleshooting for **0.1.0-beta.1**.

> [!WARNING]
> Open Alarm is Beta software and is not a certified safety system. It must not be the sole protective layer for life-safety, fire, medical, machinery-protection or other safety-critical functions.

## 1. Requirements

Open Alarm Beta.1 supports Home Assistant App installations on:

- `aarch64`
- `amd64`

The App requires Home Assistant Ingress and Supervisor/Core API access. The packaged App declares `homeassistant_api: true`, boots automatically, uses a Supervisor watchdog and participates in Home Assistant cold backups.

The current sidebar panel is admin-only. A Home Assistant administrator must open the App through Home Assistant Ingress.

## 2. Install from the App repository

1. Open **Settings → Apps → App store** in Home Assistant.
2. Open the App-store repository menu.
3. Add:

   `https://github.com/lurulude/open-alarm`

4. Refresh the App store.
5. Install **Open Alarm**.
6. Start the App.
7. Open **Open Alarm** from the Home Assistant sidebar.

The Beta repository currently builds the App container from source on the Home Assistant machine. A first install/rebuild can therefore take several minutes on smaller hardware.

### First administrator

Open Alarm does not trust Ingress identity alone as proof of administrator status. The Ingress user is verified against Home Assistant.

The first verified Home Assistant administrator to open Open Alarm becomes the first Open Alarm **Admin**. Other verified Home Assistant administrators are created with least privilege and can be assigned an Open Alarm role by an Open Alarm Admin.

The Open Alarm role hierarchy is:

- **Viewer** — read alarms, history and runtime state.
- **Operator** — Viewer capabilities plus acknowledgement, reset and shelving.
- **Engineer** — Operator capabilities plus engineering configuration, suppression and out-of-service controls.
- **Admin** — Engineer capabilities plus revision activation and Open Alarm user-role administration.

Because the Home Assistant panel is `panel_admin: true` in Beta.1, standard sidebar access is currently limited to Home Assistant administrators even though Open Alarm keeps the more detailed internal role model.

## 3. First boot

A fresh install with no active alarm configuration is valid.

Expected behavior:

- the App remains running;
- `/data/open_alarm.db` is created;
- database migrations complete before normal UI use;
- the notification worker starts;
- runtime status reports no active revision;
- `/healthz` reports healthy to the Supervisor watchdog;
- UI/API traffic is accepted through Home Assistant Ingress;
- Home Assistant receives `sensor.open_alarm_unacknowledged` and `binary_sensor.open_alarm_attention` states.

## 4. Engineering model

Open Alarm intentionally presents one engineering alarm table rather than separate Tag, Equipment and Compile editors.

The user-facing workflow is:

`EDIT → SAVE → REVIEW → ACTIVATE`

Each row represents one Home Assistant source and receives the next numeric Alarm ID automatically.

Internally, engineering row `1` uses tag `T1`. An analog row may generate one or more runtime alarms such as `A1_HIHI`, `A1_HI`, `A1_LO` and `A1_LOLO`. Those generated IDs are stable engine identities, not operator-facing alarm descriptions.

### Save

**Save all** atomically replaces the working draft's generated source tags, alarms and notification policies. Open Alarm uses optimistic draft timestamps so a stale browser session cannot silently overwrite a newer save.

### Review

**Review changes** validates the saved configuration and creates or reuses an immutable candidate revision. Invalid drafts do not create an activatable revision.

Review validates Home Assistant entity-ID syntax, configuration relationships, timings and alarm rules. It deliberately does **not** fetch every live Home Assistant state to prove that each entity currently exists. Runtime monitoring handles missing/disappearing sources as bad quality.

### Activate

**Activate revision** makes the reviewed immutable revision active and reloads runtime monitoring.

Active engineered alarms do not block activation. If an alarm with the same stable ID remains runtime-compatible, its live state is migrated. Removed or incompatible engineered alarm runtime state is reset and the new definition is evaluated. Existing event/history records are retained.

## 5. Configure an analog alarm

1. Open **Engineering**.
2. Select **Add row**.
3. Select a Home Assistant source from the searchable picker, or type a valid entity ID manually.
4. Choose **Analog**.
5. Enter at least one limit: **HiHi**, **Hi**, **Lo** or **LoLo**.
6. When multiple limits are configured, values must descend:

   `HiHi > Hi > Lo > LoLo`

7. Configure shared **Hysteresis**.
8. Configure **ON delay** and **OFF delay** if required.
9. Choose priority/category as shown in the UI.
10. Optionally enter an operator-facing **Message**.
11. Optionally select a notification group.
12. Leave the row enabled.
13. Select **Save all → Review changes → Activate revision**.

Blank analog limit fields do not create alarms.

### Analog qualification

For `HIGH` / `HIGH_HIGH`:

- abnormal at `value >= setpoint`;
- normal again only below `setpoint - hysteresis`.

For `LOW` / `LOW_LOW`:

- abnormal at `value <= setpoint`;
- normal again only above `setpoint + hysteresis`.

ON/OFF delays require continuous qualification. If the process reverses before the deadline, the pending transition is cancelled rather than accumulating elapsed time across interruptions.

## 6. Configure a digital alarm

1. Select **Add row** and choose a source such as an `input_boolean`, binary sensor or state-like entity.
2. Choose **Digital**.
3. Choose `EQUALS` or `NOT_EQUALS`.
4. Enter the state value that defines the alarm condition.
5. Configure optional debounce and ON/OFF delay.
6. Configure priority, message and notification group as required.
7. Save, Review and Activate.

Digital timing has two layers:

1. `debounce_on_s` / `debounce_off_s` qualify the raw source state;
2. `on_delay_s` / `off_delay_s` qualify the alarm transition.

A reversal cancels the current pending debounce/transition.

For `input_boolean` presentation, Open Alarm shows human state text such as **on/off** (localized in the UI/notification) instead of exposing raw Python `True`/`False` values.

## 7. Configure a device / quality alarm

Device alarms explicitly alarm on source-quality conditions instead of treating a quality problem as a normal process value.

Supported conditions are:

- `UNAVAILABLE`
- `UNKNOWN`
- `MISSING`
- `STALE`
- `BAD_QUALITY`

Device/quality alarms use ON/OFF delays, not digital debounce.

If `stale_after_s` is configured for a source, the runtime can treat a source that has not updated within that interval as stale.

## 8. Source picker and display metadata

The Engineering source picker uses Home Assistant registry/device information for search and requests a small filtered current-state preview for visible candidates. It can show:

- entity ID;
- Home Assistant friendly name;
- current value and unit;
- device name;
- manufacturer/model when Home Assistant exposes them;
- platform/device class when available.

Manual entity IDs remain accepted so a temporary registry/picker failure does not prevent configuration work.

At runtime Open Alarm records last-known source `friendly_name` and `unit_of_measurement` with alarm-relevant state. Active alarms and new history events can therefore show operator-friendly source/value text without querying Home Assistant on every browser refresh.

Historical events created before this metadata existed cannot be retroactively enriched with data that was never stored.

## 9. Alarm lifecycle

The primary lifecycle states are:

- `NORMAL` — no alarm lifecycle condition is pending or active.
- `PENDING_ON` — abnormal condition is waiting for ON delay.
- `ACTIVE_UNACK` — alarm is active and needs acknowledgement.
- `ACTIVE_ACK` — active alarm has been acknowledged.
- `PENDING_OFF` — return condition is waiting for OFF delay.
- `RTN_UNACK` — process condition returned but acknowledgement is still required by the lifecycle.

Shelving, suppression, inhibition, out-of-service and latching are separate control state. They do not replace the lifecycle state itself.

### Unacknowledged semantics

The **Unacknowledged** browser view counts:

- `ACTIVE_UNACK`;
- `RTN_UNACK`;
- an alarm in `PENDING_OFF` whose pending transition originated from `ACTIVE_UNACK`.

The count excludes shelved, suppressed, inhibited and out-of-service alarms. The same definition is used by Companion notification context, `sensor.open_alarm_unacknowledged` and the optional corner indicator.

## 10. Operator controls

### Acknowledge

An Operator can acknowledge a single alarm or acknowledge all currently eligible unacknowledged alarms.

### Reset

Reset is available to Operators for alarm states/policies that require a manual reset. The engine validates whether reset is legal for the current state.

### Shelve

Operators can shelve an alarm for a bounded duration. Shelving is temporary and can be removed manually before expiry.

### Suppress

Engineers can suppress engineered alarms. Suppression remains until explicitly removed.

### Out of service

Engineers can take engineered alarms out of service and later return them to service.

### Reasons and audit

A reason is optional for current shelving/suppression/out-of-service workflows. When entered, it is stored with the audited action. The authenticated Open Alarm/Home Assistant user and event time are recorded independently of the optional reason.

Built-in system alarms can be acknowledged but cannot be shelved, suppressed or taken out of service.

## 11. Built-in system alarms

Open Alarm includes system alarms for faults in the alarm-management path itself:

- **Home Assistant connection lost** — P1, with a short ON delay to avoid transient disconnect noise;
- **Active configuration could not be loaded** — P1;
- **Notification delivery worker stopped** — P1;
- **Notification delivery failed** — P2.

These alarms use the same persisted lifecycle/history model but are not part of the editable engineering table.

## 12. Restart safety and quality behavior

Pending alarm and digital-debounce deadlines are persisted when they matter. After an App restart, Open Alarm reloads persisted state, compares it with the current Home Assistant value and continues, completes or cancels the transition. A pending timer is not simply restarted from zero.

Home Assistant `unavailable`, `unknown`, a missing entity, stale data and Home Assistant connection loss are treated as explicit bad quality.

Bad quality does **not** clear an existing process alarm to normal. The process lifecycle is retained while a device/quality or built-in system alarm represents the fault.

## 13. Notification groups

Notification recipients are configured once as groups instead of being repeated on every alarm row.

1. In Engineering, add a notification group.
2. Give it an operator-facing **name**.
3. Set the **notification title** displayed by the recipient integration.
4. Select one or more discovered Home Assistant `notify.*` entities.
5. Configure an optional notification delay.
6. Select that group on each alarm row that should notify it.
7. Save, Review and Activate.

### Notification text

The current activation notification is intentionally compact:

- configured group title only in the notification title;
- operator Message when configured, otherwise Home Assistant friendly source name;
- localized alarm condition such as **high**, **low-low**, **korkea**, **erittäin matala**;
- current value/unit for analog sources;
- localized state text for common boolean/digital values;
- up to three other alarms that still require acknowledgement.

Internal generated IDs such as `A2_LOLO` are not used as the main operator-facing phone text.

### Delivery semantics

Alarm transition and notification-outbox enqueueing are committed together. Delivery happens after the database transaction succeeds.

Delayed notifications are revalidated before send. A stale activation is cancelled if the alarm returned or became hidden/ineligible before the delay expired. Failed deliveries remain in the persistent outbox and are retried according to the worker policy; persistent failures raise the built-in notification-delivery system alarm.

### Mobile deep link / actionable ACK limitation

Current notification groups use Home Assistant's generic `notify.send_message` route and intentionally send only **title + message** to the target `notify.*` entities.

Integration-specific mobile notification `data` is not forwarded for group delivery. Therefore Beta.1 group notifications do **not** provide:

- tap-to-open Open Alarm deep linking;
- mobile actionable ACK buttons.

Adding those features would require a separate mobile-app-specific delivery path rather than pretending the generic group transport supports them.

## 14. Home Assistant alarm-attention states

While running, Open Alarm publishes two lightweight Home Assistant states through the Supervisor/Core API:

### `sensor.open_alarm_unacknowledged`

State: integer number of alarms that currently require acknowledgement.

Attributes include a friendly name and `mdi:alert` icon.

### `binary_sensor.open_alarm_attention`

State:

- `on` when the unacknowledged count is greater than zero;
- `off` when it is zero;
- `unavailable` on a clean Open Alarm stop.

The state uses `device_class: problem` and carries the unacknowledged count as an attribute while available.

Open Alarm refreshes these states periodically while running. They can be referenced from Home Assistant dashboards and automations without installing any frontend extension.

## 15. Optional always-visible corner indicator

Open Alarm includes `open_alarm/open_alarm_indicator.js`, an optional Home Assistant frontend module.

Behavior:

- zero unacknowledged alarms: hidden;
- one or more: red `⚠ N` in the top-right corner;
- clean Open Alarm stop/unavailable state: amber `⚠ ?`;
- selecting the indicator navigates to Open Alarm.

### Enable it

1. Copy `open_alarm/open_alarm_indicator.js` from this repository to Home Assistant:

   `/config/www/open_alarm_indicator.js`

2. Add the module to `configuration.yaml`:

```yaml
frontend:
  extra_module_url:
    - /local/open_alarm_indicator.js
```

3. Restart Home Assistant Core.
4. Hard-refresh/reload the browser or Companion frontend.

### Why it is optional

The App does not request write access to `/config` merely to install a visual overlay. The module is a user-managed opt-in frontend extension.

The module is syntax-checked in CI. Home Assistant's module loader is supported, but a fixed global overlay necessarily interacts with frontend DOM/navigation behavior and may need adjustment after a major Home Assistant frontend redesign.

If the overlay does not appear, first verify `sensor.open_alarm_unacknowledged` exists and changes correctly. The native state is the source of truth; the overlay is only a presentation layer.

## 16. History

History records alarm lifecycle and operator-control events. New events include source friendly-name/unit metadata when available. Operator-facing History resolves localized condition labels and Open Alarm user display names rather than relying on generated alarm IDs and internal user IDs.

Internal IDs remain useful for troubleshooting/search and are retained in stored data.

## 17. Localization

Open Alarm currently ships English and Finnish UI/backend catalogs.

The selected Open Alarm user locale controls operator-facing UI and the locale stored with saved notification-group configuration. Persistent alarm states, event codes and IDs remain language-neutral.

Home Assistant-provided friendly names are displayed as Home Assistant supplies them; Open Alarm does not translate the user's entity names.

## 18. Persistence and database

The primary database is:

`/data/open_alarm.db`

SQLite uses WAL mode. Open Alarm stores:

- engineering drafts and generated objects;
- immutable compiled revisions and active-revision pointer;
- alarm-relevant runtime state;
- pending ON/OFF/debounce deadlines;
- source friendly-name/unit metadata for runtime state;
- shelving, suppression and out-of-service state;
- alarm history and configuration/operator audit;
- Open Alarm users, roles and locale preferences;
- notification outbox and retry state;
- runtime start/stop/connectivity events.

Database migrations are applied automatically at startup and recorded in the schema-migration table.

Beta releases may still change or rewrite the schema. Back up before upgrading between Beta versions when the release notes call for it.

## 19. Backup and restore

The App declares `backup: cold`. Use Home Assistant's normal backup workflow to back up the App. Cold backup stops the App while its data is captured, avoiding a live SQLite/WAL copy race.

Before a risky Beta upgrade:

1. create a Home Assistant backup containing Open Alarm;
2. verify the backup completed;
3. install/update Open Alarm;
4. start the App and confirm health/runtime state;
5. test one known alarm path.

For restore, use Home Assistant's App-backup restore mechanism rather than copying an active SQLite database while the App is running.

## 20. Update

When a newer version is published:

1. read `CHANGELOG.md` for migration/behavior notes;
2. create a backup if the release changes persistence/configuration behavior;
3. refresh the App store/repository;
4. update/rebuild Open Alarm through Home Assistant;
5. start it and verify `/healthz`/runtime status in the UI;
6. confirm the active revision and monitored entity count;
7. test a representative analog or digital alarm;
8. if using the optional corner indicator and its file changed, replace `/config/www/open_alarm_indicator.js` and refresh Home Assistant frontend caches.

The current source-built repository may take longer to update than an App using prebuilt registry images.

## 21. Troubleshooting

### App does not appear in the App store

- Confirm the repository URL is exactly `https://github.com/lurulude/open-alarm`.
- Refresh/reload the App store after adding the repository.
- Confirm your Home Assistant machine architecture is `aarch64` or `amd64`.
- Check repository visibility/network access if testing before public release.

### App build fails

Because Beta.1 builds from source, installation needs network access to obtain the base images and package dependencies. Review the Home Assistant App build log for Docker/npm/pip errors and retry after correcting network/storage problems.

### App starts but runtime says no active configuration

This is normal on a fresh install. Create/save/review/activate an Engineering revision.

### Entity picker does not load

The picker depends on Home Assistant registry/device WebSocket calls. Manual entity IDs remain allowed. A picker failure should not require a full Home Assistant `get_states` download during Review.

### Alarm source is shown as the raw entity ID

Open Alarm prefers runtime `friendly_name`. Confirm the Home Assistant state exposes a friendly name, verify the current App build is running, and let the source emit/update its current state. If no friendly name is available, the entity ID is the correct fallback.

### Unit is missing

Units come from Home Assistant `unit_of_measurement`. If the integration/entity does not expose a unit, Open Alarm cannot invent one. Older historical events created before metadata storage may also have no unit.

### Notification group is empty or cannot be saved

A group requires at least one valid Home Assistant `notify.*` entity target. Verify Home Assistant exposes the expected notify entity and reload the Engineering recipient list.

### Notifications work but tapping does not open Open Alarm

That is the current Beta design. Generic group delivery uses `notify.send_message` title/message and does not forward mobile-specific deep-link/action data.

### `sensor.open_alarm_unacknowledged` is wrong

Compare it to Open Alarm's **Unacknowledged** browser tab. Shelved, suppressed, inhibited, out-of-service and acknowledged active alarms are intentionally excluded.

### Corner indicator is missing

1. Verify `/config/www/open_alarm_indicator.js` exists.
2. Verify `frontend.extra_module_url` contains `/local/open_alarm_indicator.js`.
3. Restart Home Assistant Core after changing `configuration.yaml`.
4. Hard-refresh the browser/Companion frontend.
5. Confirm `sensor.open_alarm_unacknowledged` is greater than zero.
6. Check browser developer-console errors if the native state is correct but the overlay is absent.

### Suppress / out-of-service button appears not to work

Reasons are optional. The action should work without typing a reason. After changing the control state, check the matching **Suppressed** or **Out of service** browser view.

### Home Assistant is unavailable

Open Alarm keeps the current process-alarm lifecycle instead of falsely clearing it. A Home Assistant connection-loss system alarm appears after its short delay. When connectivity returns, monitored states are reconciled.

## 22. Verification checklist

Before relying on a Beta build in a real installation, verify the behavior important to that installation:

- drive analog HiHi/Hi/Lo/LoLo conditions through activation, acknowledgement and return;
- verify hysteresis around at least one threshold;
- verify ON/OFF delay cancellation when the process reverses before the deadline;
- drive a digital alarm through debounce and ON/OFF delay;
- restart the App while a timer is pending and confirm the original deadline is preserved;
- make a source unavailable and confirm bad quality does not falsely clear an active process alarm;
- acknowledge an active alarm, restart the App and confirm acknowledgement survives;
- test shelving and automatic/manual unshelving;
- test suppress/unsuppress and out-of-service/return-to-service;
- activate an engineering change while an alarm is active;
- verify notification delivery and delayed-notification cancellation;
- verify `sensor.open_alarm_unacknowledged` follows the browser count;
- if enabled, verify the corner indicator appears/disappears and opens Open Alarm;
- verify a normal stop/start keeps the same `/data/open_alarm.db` and active revision.

## 23. Local development install

For development on Home Assistant OS, copy the complete `open_alarm` directory to:

`/addons/open_alarm`

Then refresh **Local Apps** and install/rebuild the local App. Local Apps build from the checked-out source because `config.yaml` currently has no `image` key.

The development database remains `/data/open_alarm.db` inside the App data volume.

## 24. Reporting problems

For non-sensitive bugs, open a GitHub issue with:

- Open Alarm version;
- Home Assistant version and hardware architecture;
- whether the problem is runtime, Engineering, notifications or the optional overlay;
- relevant App logs;
- concise reproduction steps.

Do not post secrets, Supervisor tokens, private notification payloads or an unredacted database publicly.

For security vulnerabilities, follow the private reporting guidance in the repository `SECURITY.md`.

## 25. Source and license

Source repository:

`https://github.com/lurulude/open-alarm`

Open Alarm is licensed under the **Apache License 2.0**. The complete license text is provided in the repository root `LICENSE` file.
