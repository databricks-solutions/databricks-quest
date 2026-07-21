# 12 — Security, Governance, and Cost

## Security principles

1. **Least privilege by default.** Validators should run with only the permissions they need.
2. **Team isolation.** Teams should not be able to tamper with other teams' resources.
3. **Validation safety.** Player submissions must not result in arbitrary privileged code execution.
4. **Audit everything.** Event actions, validation attempts, hints, and score adjustments must be auditable.
5. **Workspace-local deployment.** MVP should avoid cross-account or multi-tenant trust complexity.

## Identity model

Use the Databricks App forwarded identity headers to identify the current user, as the current app does.

Map users to event roles:

- player
- team captain
- host
- admin

Role assignments are stored in Lakebase.

## Permission model (as implemented — PR04/PR10)

The running app enforces three concentric rings. Identity comes from the
Databricks App forwarded headers (`X-Forwarded-Email`/`X-Forwarded-User`),
falling back to `QUEST_DEFAULT_USER` for local dev.

| Ring | Surface | Enforced by | Rule |
|------|---------|-------------|------|
| Admin | `/api/admin/*` | `require_admin` | Caller must be in the effective admin set: `QUEST_ADMIN_ALLOWLIST` (env, deploy-time) ∪ the shared `quest_admins` Lakebase table. **Fail-open only when no admin is configured anywhere** (empty env + empty/unreachable table) for local-dev/legacy parity. |
| Host | `/api/host/*` | `require_host` | Event Mode must be enabled (else `404 EVENT_MODE_DISABLED`); caller must be in `QUEST_HOST_ALLOWLIST` when that env is set (else open, matching `/api/admin`). `require_master_host` additionally `404`s on a child deployment for master-only surfaces (roster, workspace health). |
| Player | `/api/events/*` | server-resolved identity | A player acts only as themselves. The team they submit for is resolved **server-side** from `(event_id, user)` — never taken from the request body. Validator template slots (`${team_catalog}`, `${team_schema}`, …) are filled from that resolved team, and the SQL safety layer rejects any `${...}` slot the server did not provide, so a player cannot redirect a check at another team's namespace. |

Notes / current limitations:

- The host ring is currently a single global allowlist: any host can act on any
  event in the deployment. Per-event host ownership (the `event_hosts` table) is
  modelled in the schema for a future finer-grained check; today it records
  ownership for reporting but is not a gate.
- Event Mode is opt-in. With it off, every GameDay surface (`/api/host/*`,
  `/api/events/*`) behaves as if it does not exist (`404`), so a legacy adoption
  deployment exposes no GameDay attack surface.

## Validation & SQL safety (PR03/PR10)

- `sql_assertion` validators are **read-only by default**: only a single
  `SELECT`/`WITH` statement is allowed. DDL/DML/admin verbs
  (`INSERT/UPDATE/DELETE/MERGE/DROP/ALTER/CREATE/GRANT/TRUNCATE/CALL/…`) are
  blocked, comments are stripped before analysis, and stacked statements
  (`;`-separated) are rejected — defending against stacked-query injection.
- Template values are scrubbed of statement-breaking metacharacters; an
  unresolved or unsafe slot is a hard error.
- Resource bootstrap/reset is gated by `services/namespace.py`: a destructive
  action can only ever touch a schema the event's namespace computes (never
  `system`/`main`/`hive_metastore`, a bare catalog, a wildcard, or another
  event's schema). See PR08.
- Validators never raise to the player: any failure normalises to a player-safe
  `error` outcome; raw diagnostics are persisted to
  `validation_results.private_message` for the host only.

## Audit & observability (PR10)

- **Audit:** every host/admin/player mutation writes a row to `event_audit_log`
  (lifecycle transitions, team/participant management, manual score adjustments,
  announcements, quest-pack imports, attempt submissions, and resource
  bootstrap/reset — including refused resets).
- **Request correlation:** every request gets an `X-Request-ID` (honouring an
  inbound one), echoed on the response and embedded in every error envelope
  (`{"error": {"code", "message", "request_id"}}`), so a player-reported failure
  ties to exact server logs.
- **Structured logs:** each validator outcome and scoring decision emits one
  `key=value` line (ids, type, status, point delta, awarded/idempotent) with no
  player payloads.
- **Health:** `GET /api/health` reports per-subsystem checks — Lakebase
  (with latency), migrations applied, registered validator types, scoring
  reachability, and SQL-warehouse configuration.

## Service principals

Recommended service principals:

| Principal | Purpose | Permissions |
|---|---|---|
| App SP | Serve API and read/write Lakebase | Lakebase app schema access |
| Validator SP | Run validators and inspect workspace objects | scoped to event resources and allowed system tables |
| Bootstrap SP | Create/reset event resources | scoped to configured event catalog/schema |

MVP can use one app/validator principal if necessary, but the architecture should not assume one forever.

## Resource isolation

Recommended pattern:

```text
catalog: quest_events
schema per event/team: evt_<event_slug>__team_<team_slug>
```

Or, for customers with catalog creation constraints:

```text
catalog: existing_catalog
schema: quest_<event_slug>_<team_slug>
```

Every validator should use templated team variables rather than trusting arbitrary user-submitted object names.

## SQL validator safety

Default SQL validator rules (enforced by `app/validators/safety.py::ensure_safe_select`):

- allow **only** read-only `SELECT` and `WITH … SELECT` statements
- block everything else — `DROP`, `DELETE`, `TRUNCATE`, `ALTER`, `GRANT`, `REVOKE`, `CREATE`, `INSERT`, `UPDATE`, `MERGE`, and also `SHOW`/`DESCRIBE`/`EXPLAIN` (the allowlist is intentionally narrower than the SQL surface so a validator can never branch into a non-`SELECT` form)
- block multiple/stacked statements by default
- resolve only the server-provided template slots (`${team_catalog}`, `${team_schema}`, `${event_id}`); any other `${…}` slot is refused
- validate catalog/schema identifiers (letters/digits/underscores only)
- apply a per-validator timeout
- truncate returned evidence (host sees diagnostics in `validation_results.private_message`)

## Notebook/Python validator safety

Notebook and Python validators are higher risk.

Controls:

- only hosts can author/import them
- require explicit `trusted_validator: true`
- run in controlled serverless job/task environment
- pass only scoped parameters
- output must match normalized JSON contract
- timeouts are mandatory
- raw output stored in restricted logs only

## Anti-cheat considerations

MVP controls:

- team resource isolation
- scoring idempotency
- audit logs
- attempt limits
- hint penalties
- manual host review
- validation evidence

Future controls:

- anomaly detection over attempts
- suspicious cross-team object access
- unusually fast repeated passes
- copied object lineage
- validation tamper detection

## Governance

Unity Catalog should be used to:

- scope event resources
- manage grants
- track lineage where applicable
- store shared datasets
- separate event data from operational app data

Recommended catalogs:

```text
quest_app        # app audit and analytics tables
quest_events     # event participant/team resources
quest_content    # optional quest pack content assets
```

## Cost controls

Cost risks:

- too many validation jobs
- excessive SQL Warehouse usage
- large system-table queries
- abandoned serving endpoints
- team-created resources not cleaned up

Controls:

- use serverless where practical
- dry-run validators before event
- cap attempts per task
- cache static quest content
- run quick validators synchronously
- batch heavy validators
- enforce cleanup
- stop/delete event-created resources
- show cost estimate in host console

## Observability

Add logs/metrics for:

- API request latency
- Lakebase query latency
- validation queue depth
- validation duration
- validation pass/fail/error rate
- scoring events per minute
- leaderboard refresh time
- failed DB connections
- event resource bootstrap failures

## Audit trail

Audit events:

- quest pack imported
- event created/started/paused/frozen/completed
- team created
- participant joined
- attempt submitted
- validation run completed
- hint taken
- score adjustment applied
- announcement sent
- resources reset

## Data retention

Suggested defaults:

| Data | Retention |
|---|---:|
| operational active event state | retained until archived |
| attempt and validation facts | 12–24 months |
| raw validation logs | 30–90 days |
| post-event reports | customer/account policy dependent |
| team temporary resources | deleted after event unless retained |

## Security acceptance criteria

- Players cannot call host endpoints.
- Players cannot submit validation against another team's namespace.
- SQL validators reject destructive SQL by default.
- Every score adjustment has an audit record.
- Every validation result has an attempt record.
- Event freeze blocks new attempts.
- Cleanup never drops schemas outside configured event namespace.
