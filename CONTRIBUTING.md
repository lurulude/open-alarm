# Contributing to Open Alarm

Open Alarm is intentionally small and Beta-focused. Contributions are welcome when they improve the current alarm-management product without adding unnecessary framework, compatibility or deployment complexity.

## Project principles

Prefer the simplest implementation that preserves the alarm-system fundamentals:

- deterministic alarm lifecycle behavior;
- restart-safe timers/debounce;
- transactional persistence;
- configuration validation and immutable activation revisions;
- explicit Home Assistant source quality;
- clear operator-facing UI and localization;
- tested Home Assistant/App integration.

Avoid speculative clustering, multiple databases, enterprise abstraction layers, compatibility shims for removed Beta behavior or large dependency stacks without a concrete requirement.

## Before changing behavior

For alarm-engine, activation, persistence, notification-routing or authorization changes, first identify the invariant being changed and add/update regression coverage for it.

Examples of invariants that must remain protected:

- bad quality does not falsely clear an active process alarm;
- ON/OFF/debounce delays require continuous qualification;
- persisted pending deadlines survive restart;
- acknowledged state survives restart when applicable;
- active alarms do not block valid engineering activation;
- Engineering Save is atomic;
- Review cannot activate an invalid configuration;
- Open Alarm role checks cannot be bypassed through direct API calls;
- notification delivery cannot create stale/duplicate operator messages due solely to retry/restart behavior.

## Development setup

### Backend

Python 3.13 is used in CI.

```bash
python -m pip install -r open_alarm/requirements.txt 'httpx2>=2.12,<3' pytest ruff
python open_alarm/license_audit.py fastapi uvicorn pydantic websockets httpx2 pytest ruff
ruff check open_alarm tests
pytest -q
```

Runtime dependencies are intentionally small and live in `open_alarm/requirements.txt`. Do not add a dependency when the standard library or an existing dependency solves the problem clearly.

### Frontend

Node 24 is used in CI.

```bash
cd open_alarm/frontend
npm install --no-audit --no-fund
node license-audit.mjs node_modules
npm run build
node --check ../open_alarm_indicator.js
```

The optional `open_alarm_indicator.js` is a standalone Home Assistant frontend module and is not bundled into the React App.

### Packaged App

```bash
docker build \
  --build-arg BUILD_VERSION=0.1.0-beta.2 \
  --build-arg BUILD_ARCH=amd64 \
  -t open-alarm ./open_alarm
```

The CI App smoke test boots the real packaged image, verifies health/database creation, verifies the packaged license inventories/notices, restarts it with the same `/data` volume and checks migration/integrity state.

## Code layout

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full runtime design. The main implementation areas are:

- `open_alarm/backend/domain/` — lifecycle/evaluation rules;
- `open_alarm/backend/runtime/` — runtime orchestration and Home Assistant state dispatch;
- `open_alarm/backend/db/` — persistence, queries and activation;
- `open_alarm/backend/engineering/` — user alarm table → draft objects;
- `open_alarm/backend/config/` — typed configuration and compiler;
- `open_alarm/backend/ha/` — Home Assistant WebSocket/REST clients;
- `open_alarm/backend/notifications/` — notification routing/outbox/delivery;
- `open_alarm/frontend/src/` — operator/engineering UI;
- `tests/` — backend/runtime/API regression suite.

## Engineering-model changes

The public Beta engineering model is deliberately one table. Do not introduce separate user-facing Tag, Equipment, Template or Compile editors unless a concrete product requirement makes the one-table model impossible.

An Engineering change should preserve:

`Save → Review changes → Activate`

Review is the internal compile/validation boundary. Do not add another user-visible build/promotion step solely to mirror industrial HMI terminology.

## Database changes

SQLite migrations live in `open_alarm/backend/db/migrations/` and are applied automatically in numeric order.

During Beta, schema changes may be breaking, but they still need to be explicit and testable. When adding a migration:

- increment the migration number;
- update database/migration tests;
- preserve transactional integrity;
- document operator-visible upgrade/backup requirements in `open_alarm/CHANGELOG.md`;
- do not add long-lived backward-compatibility code unless the release actually requires it.

Never silently delete alarm history or audit data as a side effect of a normal engineering activation.

## Home Assistant integration changes

Prefer scoped Home Assistant calls/subscriptions. The runtime primary path monitors only configured entities using filtered `subscribe_entities`.

Do not reintroduce a full Home Assistant `get_states` request into normal Engineering Review or 1-second alarm-browser paths. Large Home Assistant installations can exceed WebSocket message limits and make unbounded state downloads unreliable.

If a fallback genuinely requires a broad state snapshot, keep it isolated and test the primary scoped path.

## Notification changes

The supported Beta Engineering notification-group transport uses Home Assistant `notify.send_message` with target `notify.*` entities and title/message content.

Do not document or assume mobile deep links/actionable ACK buttons on this generic group path. If a future change adds mobile-specific delivery, keep that transport explicit rather than leaking integration-specific payloads into every notification group.

Notification changes should test:

- operator text/localization;
- value/unit or digital-state presentation;
- unacknowledged-only context;
- delay/revalidation behavior;
- retry/idempotency expectations;
- no generated internal alarm IDs in primary operator-facing text unless intentionally required for diagnostics.

## Third-party code, assets and dependencies

Every contribution must have a clear right to redistribute everything it adds.

Do **not** paste or adapt source from Stack Overflow, blogs, gists, closed-source products, commercial SDKs, other repositories, generated vendor bundles, icons, fonts or images unless the upstream source and license have first been identified and the license permits Open Alarm's public distribution. Required copyright/attribution/license notices must be retained and documented.

A new Python/npm dependency is acceptable only after its actual installed license passes the project audit. Open Alarm currently rejects proprietary, commercial-only, noncommercial-only, field-of-use-restricted, source-available-but-restricted and unknown-license dependencies. Do not widen the allowlist just to make CI green: review the upstream license first.

When adding or changing a dependency:

- explain why an existing dependency or the standard library is insufficient;
- run both dependency-license audits;
- update `open_alarm/THIRD_PARTY_NOTICES.md` when the release-visible dependency graph changes;
- retain any required license/copyright/NOTICE material in the distributed artifact;
- consider the effect on both source distribution and any future prebuilt container-image distribution.

Home Assistant public API/configuration identifiers may be used for interoperability. Do not copy substantial Home Assistant developer-documentation prose or code examples into Open Alarm; that documentation is separately licensed.

AI-assisted code must not be described as copied from a particular upstream project unless that source is actually known. If a generated or submitted implementation appears to reproduce distinctive third-party code and the provenance/license cannot be established, replace it with a clean implementation rather than guessing. See [PROVENANCE.md](PROVENANCE.md).

## Localization

English and Finnish catalogs must stay in parity.

When adding/changing operator-facing UI text:

- add/update both locale keys;
- use translation keys instead of hardcoded English in the React UI;
- keep lifecycle/event/config IDs language-neutral;
- do not translate Home Assistant entity friendly names supplied by the user's installation.

## Tests expected by change type

### Lifecycle/evaluation

Add focused unit tests for each transition edge and cancellation path affected.

### Restart/persistence

Test serialized/persisted state and restore behavior, including original deadlines.

### Engineering/compiler

Test valid expansion plus invalid input and transaction/conflict behavior.

### API/authorization

Test allowed and forbidden roles and the error path.

### Notifications

Test payload/operator text and outbox behavior.

### Frontend/i18n

Ensure the npm license audit and `npm run build` pass and translation coverage remains complete.

### Home Assistant App packaging

If App manifest, Dockerfile, startup, static frontend packaging or health behavior changes, the packaged App smoke test and packaged license verification must still pass.

## Pull requests

External contributors should use a focused branch/fork and open a pull request against `main`.

Keep a PR narrow enough to review. Include:

- what problem it solves;
- operator/runtime behavior before and after;
- tests added/changed;
- migration/backup implications, if any;
- new dependencies or third-party material and their licenses, if any;
- screenshots only when they materially help evaluate UI behavior.

There is no required commit-message format. Clear, small commits are preferred while the work is under review; maintainers may squash when appropriate.

## Documentation changes

User-facing changes should update the relevant documentation in the same contribution:

- repository overview: `README.md`;
- App Store intro: `open_alarm/README.md`;
- usage/operations: `open_alarm/DOCS.md`;
- architecture/invariants: `ARCHITECTURE.md`;
- release notes: `open_alarm/CHANGELOG.md`;
- third-party dependency/attribution inventory: `open_alarm/THIRD_PARTY_NOTICES.md` when applicable;
- source-provenance policy: `PROVENANCE.md` when applicable.

Do not leave roadmap/speculative features in release documentation. The working tree should describe the program that actually exists.

## Security

Do not open a public issue/PR with a live exploit, credential, Supervisor token or unredacted private database. Follow [SECURITY.md](SECURITY.md) for private vulnerability reporting.

## License

Open Alarm project source is Apache-2.0 licensed. By contributing code or documentation, you must have the right to submit it and understand that accepted project contributions are distributed under the repository's Apache-2.0 license. Third-party material retains its own license and is never automatically relicensed merely because it is referenced by Open Alarm.
