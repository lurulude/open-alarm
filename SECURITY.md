# Security Policy

Open Alarm is a Home Assistant OS App that handles authenticated operator actions, Home Assistant API access, alarm configuration and persistent operational history. Security reports are welcome.

## Supported versions

Open Alarm is currently in Beta. Security fixes are provided for the **latest published Beta release** only unless a release note explicitly says otherwise.

| Version | Supported |
| --- | --- |
| latest `0.1.0-beta.*` | Yes |
| older development / Beta builds | No |

During Beta, users should update to the latest published version after reviewing release notes and taking a Home Assistant backup when recommended.

## Reporting a vulnerability

Please do **not** open a public GitHub issue containing exploit details, credentials, Supervisor tokens, private notification payloads, database contents or other sensitive information.

Preferred reporting path:

1. Open the repository **Security** tab.
2. Use GitHub's private **Report a vulnerability** / private vulnerability reporting flow when it is available.
3. Include enough detail to reproduce and assess the issue without including unrelated private data.

If private vulnerability reporting is not available, contact the repository owner through GitHub and request a private reporting channel before sharing exploit details.

For non-sensitive bugs, use the normal GitHub issue tracker.

## Useful report information

A useful security report normally includes:

- affected Open Alarm version/commit;
- Home Assistant version and installation architecture;
- attack prerequisites and required permissions;
- exact reproduction steps or a minimal proof of concept;
- expected vs. actual authorization/security behavior;
- impact assessment;
- relevant logs with tokens, user identifiers and private entity data redacted;
- suggested mitigation, if known.

## Security-sensitive areas

Reports are especially valuable for problems involving:

- bypass of Home Assistant Ingress or administrator verification;
- Open Alarm Viewer / Operator / Engineer / Admin role bypass;
- unauthorized acknowledgement, suppression, out-of-service or Engineering activation;
- exposure or misuse of `SUPERVISOR_TOKEN`;
- arbitrary Home Assistant service/API calls beyond intended Open Alarm behavior;
- SQL injection or database corruption through API/configuration input;
- path traversal, arbitrary file read/write or container escape;
- cross-site scripting or unsafe rendering of Home Assistant/entity/operator text;
- notification data leaking to unintended recipients;
- unsafe handling of secrets in logs or error responses;
- vulnerabilities in the optional Home Assistant corner-indicator module.

## Security model notes

The Beta security boundary is intentionally narrow:

- normal UI/API access is Home Assistant Ingress-only;
- `/healthz` is the minimal direct watchdog exception;
- Ingress identity is verified against Home Assistant rather than trusted on its own;
- Open Alarm applies its own role checks to API actions;
- the Home Assistant panel is currently admin-only;
- the App uses `homeassistant_api: true` for required Supervisor/Core API access;
- the optional corner indicator is not auto-installed and the App does not request `/config` write permission for it;
- database/configuration state is stored in the App data directory rather than Home Assistant configuration files.

## Secrets and accidental disclosure

If a real credential/token is exposed, rotate or revoke it immediately even if the repository/history is later rewritten. Git history cleanup is not a substitute for secret rotation.

Do not attach an unredacted `/data/open_alarm.db` publicly. The database can contain Home Assistant entity identifiers, alarm/operator history, Open Alarm users/roles and notification configuration.

## Coordinated fixes

There is no guaranteed response-time SLA during Beta. The maintainer will triage reports on a best-effort basis, prioritize issues that can cross authorization boundaries or affect Home Assistant credentials/data, and publish a fixed Beta release when appropriate.

Security fixes may include breaking changes during Beta if that is the simplest safe correction.
