# Releasing Open Alarm

This document is the maintainer checklist for publishing Open Alarm Beta releases.

Open Alarm is a Home Assistant App repository. The App version in `open_alarm/config.yaml` is the version Home Assistant uses for update detection.

## Release principles

- Keep releases small and understandable.
- Do not claim a capability until the actual packaged App path has been tested.
- Treat alarm-engine, persistence, activation and authorization changes as safety-relevant even during Beta.
- Document breaking/migration behavior before publishing the version.
- Once the repository is public, do **not** rewrite published Git history to make it look cleaner. Use normal forward commits/releases.

## Version format

During Beta use versions such as:

`0.1.0-beta.1`

Increment the Beta suffix for a new Beta release unless the intended semantic version changes.

## Files that must agree

Before release, search the working tree for the previous version and update all intentional version references.

At minimum verify:

- `open_alarm/config.yaml` → `version`;
- `.github/workflows/test.yml` → packaged smoke `BUILD_VERSION`;
- `open_alarm/CHANGELOG.md` → new release heading/notes;
- `README.md` / development build example when it names a concrete version;
- `open_alarm/DOCS.md` when it names a concrete supported version;
- `open_alarm/README.md` when its App Store intro names the release.

`repository.yaml` does not contain the App version.

## Release checklist

### 1. Review scope

Confirm the release contains only intended current-product changes. Remove obsolete roadmap/specification text, temporary files, debug code and stale development instructions.

### 2. Database / configuration review

If the release changes schema or Engineering semantics:

- add/update numeric SQLite migrations;
- update migration/database tests;
- decide whether users should take a backup before updating;
- describe the behavior clearly in `CHANGELOG.md` and `DOCS.md`.

Do not silently delete alarm history/audit as part of a routine upgrade.

### 3. Notification review

If notification behavior changes, verify the supported transport actually delivers the documented fields.

For the current generic Engineering notification-group path, `notify.send_message` sends title/message to target `notify.*` entities. Do not advertise mobile-specific deep links or action buttons unless a separate tested mobile transport has been implemented.

### 4. Localization review

Ensure English/Finnish translation keys remain in parity and no new operator-facing English strings are hardcoded into Finnish UI paths.

### 5. Local checks

Backend:

```bash
python -m pip install -r open_alarm/requirements.txt 'httpx2>=2.12,<3' pytest ruff
ruff check open_alarm tests
pytest -q
```

Frontend:

```bash
cd open_alarm/frontend
npm install --no-audit --no-fund
npm run build
node --check ../open_alarm_indicator.js
```

Optional packaged image check:

```bash
docker build \
  --build-arg BUILD_VERSION=<release-version> \
  --build-arg BUILD_ARCH=amd64 \
  -t open-alarm-release-check ./open_alarm
```

### 6. CI gate

The final release commit must have a fully green GitHub Actions run:

- backend Ruff;
- backend pytest;
- frontend production build;
- optional indicator JavaScript syntax check;
- packaged App Docker build;
- packaged frontend verification;
- persistent App boot/restart smoke;
- SQLite migration/integrity verification.

Do not publish based on an older green commit if the release commit itself changed code, packaging or documentation checked by CI.

### 7. Real Home Assistant smoke when runtime behavior changed

For runtime/Engineering/notification changes, test the final candidate on Home Assistant OS when practical:

- App installs/builds;
- App starts and Ingress opens;
- active revision loads;
- configured source count is sensible;
- one analog or digital alarm activates/returns;
- acknowledgement works;
- restart preserves expected state/deadline;
- notifications work if changed;
- `sensor.open_alarm_unacknowledged` follows the Unacknowledged browser count;
- optional corner indicator works if changed.

A documentation-only release does not need to repeat every physical alarm test, but CI still needs to pass.

### 8. Publication metadata

Verify repository root contains:

- `repository.yaml`;
- `LICENSE` (Apache-2.0);
- `README.md`;
- `SECURITY.md`;
- `CONTRIBUTING.md`.

Verify App folder contains:

- `config.yaml`;
- `README.md`;
- `DOCS.md`;
- `CHANGELOG.md`;
- `Dockerfile`;
- `run.sh`.

Home Assistant also recommends `icon.png` and `logo.png` for better App Store presentation. They are presentation assets rather than runtime requirements; add them when final Open Alarm brand artwork is available.

### 9. Make repository/public release available

For the first public Beta, the GitHub repository must be Public so Home Assistant installations can add:

`https://github.com/lurulude/open-alarm`

The connected development tooling may not expose repository-visibility changes; change visibility in GitHub repository settings if needed.

Home Assistant does not require a Git tag for App repository update detection; `config.yaml` versioning is authoritative. A GitHub tag/release may still be created for human-readable release history if desired.

## After publication

- Do not force-rewrite public `main` history.
- Watch issues for install/build problems on both supported architectures.
- If a security-sensitive regression is discovered, follow `SECURITY.md` and publish a fixed Beta promptly rather than hiding the old commit.
- Keep release docs describing the current program, not speculative roadmap items.

## Prebuilt images later

Beta.1 intentionally builds the App from source on the Home Assistant machine.

If installation time/reliability becomes a real user problem, move to Home Assistant's recommended multi-architecture registry-image publishing flow and set an `image:` in `open_alarm/config.yaml`. Treat that as a release-engineering change: test `aarch64` and `amd64`, document registry/image naming, and keep local source-build development simple.
