# 08 — API Contract

## Principles

- Preserve existing adoption endpoints.
- Add versioned GameDay endpoints under `/api/events` and `/api/quest-packs`.
- Make write operations auditable.
- Return safe player-facing validation errors.
- Keep host-only endpoints separated.
- Use event and team scoping everywhere.

## Event Mode gating (opt-in)

GameDay is **off by default**. Every GameDay endpoint below — `/api/events/*`,
`/api/host/*`, and `/api/federation/*` — is gated on Event Mode
(`QUEST_EVENT_MODE` / `--event-mode`; implied by the `master`/`child` roles).
When Event Mode is **off**, these endpoints return:

```http
404 Not Found
{ "error": { "code": "EVENT_MODE_DISABLED",
             "message": "Event Mode is not enabled on this deployment." } }
```

The adoption endpoints (`/api/profile`, `/api/missions`, `/api/leaderboard`,
`/api/admin/*`, `/api/health`, `/api/notifications`) are always available.
`GET /api/health` reports `"event_mode": true|false` so clients can gate UI.

### Admin page gating (DB-backed, shared)

`/api/admin/*` is gated by an admin allowlist independent of Event Mode. The
durable source of truth is the Lakebase table `quest_admins`, which is **shared
across apps** — in federation the master owns it and child apps read/write it
through the shared event-writer role, so an admin is automatically an admin on
the standalone GameDay app, the master, and every child workspace.

The effective admin set is the **union** of:

- `quest_admins` (DB, source of truth), and
- `QUEST_ADMIN_ALLOWLIST` (env bootstrap/fallback — set by
  `deploy.sh --admins a@x.com,b@y.com`, defaulting to the deploying user except
  for `--role child`, which inherits admins from the master).

On startup the env allowlist is seeded into `quest_admins` (standalone/master
only). Gating behaviour:

- When the effective set is **non-empty**, users not in it get
  `403 { "error": { "code": "FORBIDDEN", "message": "Admin access required." } }`.
- When it is **empty** (no env allowlist and an empty/unreachable table, e.g.
  local dev), the endpoints stay open (prior behaviour).
- If the DB read fails, gating falls back to the env allowlist so the deployer
  keeps access.

`GET /api/profile` returns `"is_admin": true|false` for the caller so the
frontend can hide the Admin nav (defence-in-depth; the 403 gate is the real
boundary).

#### Admin management endpoints (admin-only, always available)

```http
GET    /api/admin/admins
POST   /api/admin/admins           { "email": "new@corp.com" }
DELETE /api/admin/admins/{email}
```

- `GET` returns `{ admins: [{ email, added_by, source, added_at }], caller,
  caller_is_admin }`. `source` is `manual` (added in-app), `seed` (from the
  deploy allowlist), or `env` (deploy-config-only, not in the DB).
- `POST` adds an admin (admins can grant admin). `400 INVALID_EMAIL` on a
  malformed address; `503 ADMIN_WRITE_FAILED` if Lakebase isn't writable from
  this app.
- `DELETE` removes a DB admin. Guards: `404 NOT_FOUND` if not an admin,
  `409 LAST_ADMIN` to prevent lockout, `409 ENV_ADMIN` for deploy-config admins
  (remove those via the deployment's `--admins`), and `403 REMOVE_NOT_PERMITTED`
  from a child app (the writer role has no DELETE — remove from the
  master/standalone app).

## Existing endpoints to preserve

```http
GET /api/health
GET /api/profile
GET /api/missions
GET /api/leaderboard?period=all|weekly|monthly
GET /api/notifications
GET /api/admin/stats
GET /api/admin/pipeline-status
```

### `/api/health` migration status (PR01)

`/api/health` keeps its existing fields (`status`, `db_connected`, `timestamp`)
and additionally reports GameDay migration status. The new fields are additive
and safe for existing clients to ignore:

```json
{
  "status": "ok",
  "db_connected": true,
  "migrations_applied": ["001_gameday_core"],
  "migrations_count": 1,
  "timestamp": "2026-06-03T00:00:00.000000"
}
```

`migrations_applied` is an empty list when Lakebase is unavailable or migrations
have not yet run, so health never fails because of migration state.

### `/api/health` subsystem checks (PR10)

`/api/health` additionally reports a `checks` block and `validator_types`, so an
operator can see *which* subsystem is degraded rather than a binary up/down:

```json
{
  "status": "ok",
  "db_connected": true,
  "db_latency_ms": 12.4,
  "validator_types": ["manual", "sql_assertion"],
  "checks": {
    "lakebase":      { "ok": true, "latency_ms": 12.4 },
    "migrations":    { "ok": true, "applied": 5 },
    "validators":    { "ok": true, "types": ["manual", "sql_assertion"] },
    "scoring":       { "ok": true },
    "sql_warehouse": { "ok": false, "configured": false }
  }
}
```

`status` is `degraded` only when Lakebase is down; an unset SQL warehouse is
informational (dry-run still works), not degraded.

### Request correlation & error envelope (PR10)

Every response carries an `X-Request-ID` header (an inbound one is honoured,
else a `req_…` id is minted). Every error response uses a single envelope with
that id embedded, so a player can quote it to their host:

```json
{ "error": { "code": "EVENT_NOT_ACTIVE", "message": "This event is paused…", "request_id": "req_4f1c…" } }
```

Each validator outcome and scoring decision also emits one structured
`key=value` log line (ids, type, status, point delta) — no player payloads. See
`docs/12_SECURITY_GOVERNANCE_COST.md` for the full permission model.

## New public/player endpoints

### List active events

```http
GET /api/events
```

Response:

```json
{
  "events": [
    {
      "event_id": "evt_123",
      "title": "AI/BI GameDay",
      "status": "active",
      "starts_at": "2026-06-10T09:00:00Z",
      "ends_at": "2026-06-10T12:00:00Z",
      "pack_title": "AI/BI Intelligence Challenge"
    }
  ]
}
```

### Get event lobby

```http
GET /api/events/{event_id}
```

Response includes:

- event summary
- current user registration status
- team assignment
- event phase
- countdown metadata
- latest announcements

### Join event

```http
POST /api/events/{event_id}/join
```

Request:

```json
{
  "team_code": "optional-team-code",
  "display_name": "Alistair"
}
```

### Team self-service (player)

When the event is joinable and `QUEST_TEAM_SELF_SERVICE` is enabled, players can
create and rename teams from the lobby without a host. The lobby payload exposes
`team_self_service` so the UI can show/hide the controls.

```http
POST /api/events/{event_id}/teams          # body { name, display_name? } → creates and joins a team
POST /api/events/{event_id}/team/rename    # body { display_name } → renames the caller's current team
```

### Get team dashboard

```http
GET /api/events/{event_id}/team
```

Implemented in PR05. Resolves the caller's team for the event and returns the
team gameplay dashboard. If the caller is not on a team, `team` is `null`
(`joined` still reflects participant status):

```json
{
  "joined": true,
  "team": { "team_id": "team_red", "name": "Red", "display_name": "Red Team", "color": "#FF5F1F" },
  "members": [ { "user_id": "ada@corp.com", "display_name": "Ada", "role": "player" } ],
  "score": 200,
  "rank": 2,
  "completed_task_ids": ["tsk_1"],
  "progress": { "completed_tasks": 1, "total_tasks": 6 },
  "recent": [ { "scoring_event_id": "se_1", "team_id": "team_red", "task_id": "tsk_1", "points_delta": 200, "reason": "task_base", "created_at": "..." } ],
  "attempts_open": true
}
```

`recent` is filtered to the caller's team. Score/rank derive from the
`team_scores` / `event_leaderboard` views; `completed_task_ids` are tasks the
team has been awarded positive base points for.

### List event quests

```http
GET /api/events/{event_id}/quests
```

Implemented in PR05. Returns the event pack's quests, each annotated with the
caller team's progress:

```json
{
  "quests": [
    { "quest_id": "qst_1", "slug": "ai-bi", "title": "AI/BI Challenge",
      "category": "analytics", "difficulty": "intermediate", "base_points": 0,
      "task_count": 3, "completed_tasks": 1, "complete": false }
  ],
  "team_id": "team_red",
  "attempts_open": true
}
```

`complete` is true only when `completed_tasks == task_count` (and there is at
least one task). With no team, progress counts are `0`.

### Get quest detail

```http
GET /api/events/{event_id}/quests/{quest_id}
```

Implemented in PR05. The quest runner payload — narrative, tasks (objective,
instructions, success criteria), per-task hints, and the caller team's
completion. 404s if the quest is not part of the event's pack version:

```json
{
  "quest": { "quest_id": "qst_1", "slug": "ai-bi", "title": "AI/BI Challenge", "narrative": "..." },
  "tasks": [
    {
      "task_id": "tsk_1", "slug": "build-gold", "title": "Build the gold table",
      "objective": "...", "instructions_md": "...", "success_criteria_md": "...",
      "points": 200, "validation_mode": "sql_assertion", "complete": true,
      "hints": [ { "hint_id": "hint_1", "title": "Hint 1", "body_md": "...", "penalty_points": -10, "sort_order": 1, "revealed": true } ]
    }
  ],
  "team_id": "team_red",
  "attempts_open": true
}
```

A hint's `body_md` is **withheld (`null`) until the team reveals it** via the
hint-reveal endpoint (PR07) — `revealed` reflects whether this team has already
unlocked (and been charged for) it. This stops a player reading the hint text
for free and dodging the penalty.

There is no per-task declared submission schema in the MVP: the player submits
a free-form JSON `submission` object (see *Submit task attempt*), which the
task's validators interpret. The UI defaults the editor to a small template
keyed off `validation_mode` (e.g. `catalog`/`schema` for SQL assertions,
`evidence` for manual review).

### Submit task attempt

```http
POST /api/events/{event_id}/tasks/{task_id}/attempts
```

Request:

```json
{
  "submission": {
    "catalog": "team_01",
    "schema": "silver",
    "object_name": "customer_gold"
  }
}
```

Response for sync validation (implemented in PR03):

```json
{
  "attempt_id": "att_123",
  "status": "passed",
  "message": "Task complete! +200 points.",
  "points_awarded": 200,
  "already_awarded": false,
  "results": [
    { "status": "passed", "message": "Validated successfully." }
  ],
  "team_id": "team_red"
}
```

`status` is one of `passed | failed | manual | error` (the MVP runs SQL
synchronously). `results` carries one **player-safe** message per validator; raw
validator diagnostics are persisted to `validation_results.private_message` for
the host and never returned here. A repeat passing submission returns
`status: "passed"` with `already_awarded: true` and `points_awarded: 0` — base
points are awarded once per scope (team in standalone/master, workspace in a
federation child), enforced by the `scoring_events.idempotency_key` UNIQUE
constraint. `manual` validators return `status: "manual"` (pending host review,
no points). Identity/team resolution: standalone/master resolve the team via
team membership; a federation child resolves it via the identity map and stamps
`workspace_id` so the master attributes the score after roster import.

Response for async validation (future — async validator execution):

```json
{
  "attempt_id": "att_123",
  "status": "queued",
  "message": "Validation queued. This can take up to 60 seconds."
}
```

### Get attempt status

```http
GET /api/events/{event_id}/attempts/{attempt_id}
```

Response (implemented in PR03):

```json
{
  "attempt": {
    "attempt_id": "att_123",
    "task_id": "tsk_1",
    "team_id": "team_red",
    "status": "passed",
    "submitted_at": "2026-06-03T05:00:00",
    "completed_at": "2026-06-03T05:00:01"
  },
  "results": [
    { "validation_result_id": "vres_1", "validator_id": "val_1",
      "status": "passed", "score_delta": 0,
      "public_message": "Validated successfully." }
  ]
}
```

Results expose only player-safe fields (no `private_message`).

### Reveal hint (PR07)

```http
POST /api/events/{event_id}/tasks/{task_id}/hints/{hint_id}/reveal
```

Reveals a hint's body and charges its penalty **once per team** (idempotent on
`scoring_events.idempotency_key = hint:{team}:{event}:{hint}`). Re-revealing is
free. While the event is paused/frozen/completed the body is still returned but
**no penalty is incurred** (no new scoring when play is closed), so
`penalty_applied` is `0` and `newly_applied` is `false`. The penalty is
normalised to a non-positive delta (`-abs(penalty_points)`).

```json
{
  "hint": {
    "hint_id": "hint_1",
    "title": "Hint 1",
    "body_md": "Check the table owner and grants in Catalog Explorer.",
    "penalty_points": -10
  },
  "revealed": true,
  "penalty_applied": -10,
  "newly_applied": true,
  "team_score": 1240
}
```

### Get event leaderboard (PR07)

```http
GET /api/events/{event_id}/leaderboard
```

The player-facing live leaderboard. Ranking is deterministic: higher
`total_points` first, ties broken by the team that reached the total **earliest**
(`event_leaderboard` view: `RANK() OVER (... ORDER BY total_points DESC,
last_scored_at ASC)`). `frozen` is `true` when the event is `frozen`/`completed`/
`archived` or has a `scoring_frozen_at` set (no new player scoring); the UI shows
a "Final results" / "Scoring frozen" badge. `you` highlights the caller's team
(with a `null` rank when on a team but unscored). `recent` is the activity feed
(team + task names, signed `points_delta`, including hint penalties and host
manual adjustments).

```json
{
  "event": { "event_id": "evt_1", "title": "...", "status": "active", "scoring_frozen_at": null },
  "frozen": false,
  "status": "active",
  "leaderboard": [
    { "event_id": "evt_1", "team_id": "team_blue", "display_name": "Blue", "total_points": 300, "rank": 1, "last_scored_at": "..." },
    { "event_id": "evt_1", "team_id": "team_red", "display_name": "Red", "total_points": 100, "rank": 2, "last_scored_at": "..." }
  ],
  "recent": [
    { "scoring_event_id": "score_1", "team_id": "team_blue", "team_name": "Blue", "task_id": "tsk_1", "task_title": "Build the gold table", "source_type": "validation", "points_delta": 200, "reason": "task_passed", "created_at": "..." }
  ],
  "you": { "team_id": "team_red", "display_name": "Red", "total_points": 100, "rank": 2 }
}
```

## Host/admin endpoints

### Import quest pack

```http
POST /api/host/quest-packs/import
```

Request:

```json
{
  "manifest_yaml": "..."
}
```

Response:

```json
{
  "pack_id": "pack_ai_bi",
  "pack_version_id": "packver_123",
  "status": "imported",
  "warnings": []
}
```

### Lint quest pack

```http
POST /api/host/quest-packs/lint
```

### List quest packs

```http
GET /api/host/quest-packs
```

### Get quest pack

```http
GET /api/host/quest-packs/{pack_id}
```

### Event & team lifecycle — PR04 implementation status

Implemented in PR04. All are Event-Mode-gated (404 when Event Mode is off);
host endpoints additionally enforce `QUEST_HOST_ALLOWLIST` via `require_host`.

Player endpoints:

- `GET /api/events` → `{ events: [{ event_id, slug, title, status, team_count, … }] }`
  — events visible to players (`ready`/`active`/`paused`/`frozen`).
- `GET /api/events/{event_id}` (id or slug) → lobby:
  `{ event, joinable, attempts_open, is_host, teams[], counts, you }`.
- `POST /api/events/{event_id}/join` body `{ display_name?, team_id?, team_name? }`
  → `{ joined, participant_id, team_id, team_name }`. Idempotent; `409 NOT_JOINABLE`
  unless the event is `ready`/`active`/`paused`. A participant is on **exactly one
  team per event** — naming a different team reassigns (the prior membership is
  removed), keeping scoring unambiguous.

Host endpoints:

- `POST /api/host/events` body `{ title, pack_version_id, slug?, description?,
  timezone? }` → `{ event }`. Creator is recorded as an `owner` host. `400` for an
  unknown `pack_version_id`; `409 SLUG_EXISTS` for a duplicate slug.
- `POST /api/host/events/{event_id}/teams` body `{ name, display_name?, color?,
  team_catalog?, team_schema? }` → `{ team }`. `409 TEAM_EXISTS` on duplicate name.
- `POST /api/host/events/{event_id}/participants/import` body
  `{ participants: [{ email|user_id, display_name?, team_name? }] }` →
  `{ rows, participants_created, teams_created, assignments }`. Idempotent; teams
  named in rows are created on demand.
- `POST /api/host/events/{event_id}/teams/{team_id}/members` body
  `{ user_id? | participant_id?, display_name? }` →
  `{ assigned, team_id, participant_id }`. Assigns a participant to a team
  (single team per event; reassigns if already on another). A `user_id` that
  isn't registered yet is registered on demand. `404 TEAM_NOT_FOUND` /
  `PARTICIPANT_NOT_FOUND`, `400 INVALID_ASSIGN` if neither id is given.
- Lifecycle: `POST /api/host/events/{event_id}/{start|pause|freeze|complete|ready|archive}`
  → `{ event }`. Enforces the state machine; `409 INVALID_TRANSITION` on an
  illegal move.

**State machine.** `draft → ready|active|archived`; `ready → active|archived`;
`active → paused|frozen|completed`; `paused → active|frozen|completed`;
`frozen → active|completed`; `completed → archived`. `start`→`active` stamps
`starts_at`; `complete` stamps `ends_at`; `freeze` stamps `scoring_frozen_at`;
unfreeze (`frozen→active`) clears it.

**Attempt gating.** `POST /api/events/{event_id}/tasks/{task_id}/attempts` only
accepts submissions while the event is `active`; any other status returns
`409 EVENT_NOT_ACTIVE` with a player-safe message. Every mutation above writes an
`event_audit_log` row.

### Host console — PR06 implementation status

Implemented in PR06. All host-console endpoints are Event-Mode-gated and enforce
`require_host` (the `QUEST_HOST_ALLOWLIST`). The console reuses the PR04 lifecycle
transitions for start/pause/freeze/complete and adds read/composition endpoints.

- `GET /api/host/events/{event_id}` → host dashboard:
  `{ event, attempts_open, allowed_transitions[], counts, teams[], attempt_status_counts, announcements[] }`.
  `teams[]` are `{ team_id, name, display_name, color, members, score, rank }`
  sorted by rank (unranked last). `allowed_transitions` lists the next legal
  lifecycle verbs for the current status.
- `GET /api/host/events/{event_id}/teams` → `{ teams: [{ …, members_list:[{user_id, display_name}] }] }`.
- `GET /api/host/events/{event_id}/attempts?status=&limit=` → validation queue /
  results / failed view: `{ attempts: [{ attempt_id, task_id, task_title, team_id,
  team_name, submitted_by, status, submitted_at, completed_at, error_message }], status_counts }`.
  `limit` is clamped to 1..500. `status` optionally filters to one terminal state.
- `GET /api/host/events/{event_id}/attempts/{attempt_id}` → full detail incl.
  **private** validator diagnostics (host-only): `{ attempt, results:[{ validator_id,
  status, score_delta, public_message, private_message }] }`.
- `GET /api/host/events/{event_id}/announcements` → `{ announcements[] }` (host view, 50).
- `POST /api/host/events/{event_id}/announcements` body `{ title, body_md, severity? }`
  → the created announcement. `400 INVALID_ANNOUNCEMENT` if title/body blank;
  `severity` ∈ `info|warning|critical` (anything else coerced to `info`). Audited.
- `POST /api/host/events/{event_id}/adjustments` body
  `{ team_id, points_delta, reason, task_id?, user_id? }` →
  `{ adjustment_id, scoring_event_id, points_delta }`. Writes a `manual_adjustments`
  audit row **and** a `scoring_events` ledger row (`source_type=manual_adjustment`)
  atomically, so the leaderboard reflects the change immediately. `400 REASON_REQUIRED`
  (blank reason), `400 ZERO_DELTA`, `404 TEAM_NOT_FOUND` (team not in this event).
  Audited (`score.adjust`).

Player-facing announcement feed (Event-Mode-gated, any authenticated player):

- `GET /api/events/{event_id}/announcements` → `{ announcements[] }` (latest 20).

### Quest pack endpoints — PR02 implementation status

Implemented in PR02 (lint, import, list, get):

- Request body for lint/import is `{ "manifest_yaml": "<yaml string>" }`.
- **Lint** always returns `200` with `{ ok, errors[], warnings[], summary }`.
  `summary` has `quests`/`tasks`/`validators`/`hints` counts. `errors` and
  `warnings` are `{ loc, message }`.
- **Import** lints first and refuses on errors (`400` with the error model plus
  a `lint` block). On success returns
  `{ pack_id, pack_version_id, status, counts, content_hash, warnings }`.
  `status` is `imported` for a new version or `duplicate` when identical content
  for the same `(slug, version)` was already imported (idempotent).
- **Immutability:** re-importing an existing `(slug, version)` with *different*
  content returns `400` (`QUEST_PACK_INVALID`) — bump `pack.version` instead.
- **Auth:** `/api/host/*` resolve the user from forwarded headers. An optional
  `QUEST_HOST_ALLOWLIST` env (comma-separated emails) restricts access; when
  unset the endpoints are open (parity with `/api/admin`). Full event-role
  enforcement is a later PR.

Example (against the running app, in an authenticated browser session or with
forwarded-identity headers):

```bash
# Lint the built-in sample pack
curl -sX POST "$APP_URL/api/host/quest-packs/lint" \
  -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json,sys; print(json.dumps({"manifest_yaml": open("quest_packs/built_in/ai_bi_gameday.yml").read()}))')"

# Import it
curl -sX POST "$APP_URL/api/host/quest-packs/import" \
  -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json,sys; print(json.dumps({"manifest_yaml": open("quest_packs/built_in/ai_bi_gameday.yml").read()}))')"

# List, then fetch detail
curl -s "$APP_URL/api/host/quest-packs"
curl -s "$APP_URL/api/host/quest-packs/<pack_id>"
```

Local linting without a server: `python scripts/lint_quest_pack.py [path]`.

### Create event

```http
POST /api/host/events
```

Request:

```json
{
  "title": "Customer AI/BI GameDay",
  "slug": "customer-ai-bi-gameday",
  "pack_version_id": "packver_123",
  "starts_at": "2026-06-10T09:00:00Z",
  "ends_at": "2026-06-10T12:00:00Z",
  "config": {
    "team_mode": true,
    "max_team_size": 4,
    "allow_self_team_select": false
  }
}
```

### Import participants

```http
POST /api/host/events/{event_id}/participants/import
```

Request:

```json
{
  "participants": [
    {"email": "user@example.com", "display_name": "User Name", "team": "Team 1"}
  ]
}
```

### Create teams

```http
POST /api/host/events/{event_id}/teams
```

### Start/pause/freeze/end event

```http
POST /api/host/events/{event_id}/start
POST /api/host/events/{event_id}/pause
POST /api/host/events/{event_id}/freeze
POST /api/host/events/{event_id}/complete
```

### Manual score adjustment

```http
POST /api/host/events/{event_id}/adjustments
```

Request:

```json
{
  "team_id": "team_1",
  "task_id": "task_optional",
  "points_delta": 50,
  "reason": "Bonus for best explanation during debrief."
}
```

> Dry-run is not a standalone endpoint — the bootstrap/reset dry-run is
> `POST /api/host/events/{event_id}/resources/plan` (see *Resource bootstrap &
> reset* below).

### Manage event hosts

Host authority is the union of `QUEST_HOST_ALLOWLIST`, admins, and per-event
`event_hosts` rows; in Event Mode the gate is fail-closed (deny when no authority
is configured, unless `QUEST_HOST_OPEN=1`). These endpoints manage the per-event
host list and are audited (`admin/host` privilege changes write `record_audit`).

```http
GET    /api/host/events/{event_id}/hosts            # → { hosts: [{ user_id, added_by, added_at }] }
POST   /api/host/events/{event_id}/hosts            # body { email } → adds an event host
DELETE /api/host/events/{event_id}/hosts/{email}    # removes (last-owner protected)
```

### Review validation attempts

```http
GET /api/host/events/{event_id}/attempts?status=failed
```

### Create announcement

```http
POST /api/host/events/{event_id}/announcements
```

### Resource bootstrap & reset (PR08)

Provision and tear down each team's Databricks resources (catalogs/schemas, plus
optional pack seed SQL). Every target is computed by `services/namespace.py`,
the single authority on what is in-namespace; a destructive action can only ever
touch a schema this event's namespace computes (never `system`/`main`/
`hive_metastore`, a bare catalog, a wildcard, or another event's schema).

The per-team target resolves as: `catalog` = `team_catalog` or
`config_json.resource_namespace.catalog` or `quest_<event-slug>`; `schema` =
`team_schema` or `<schema_prefix><team-name>`. These same values fill the
`${team_catalog}` / `${team_schema}` validator slots, so bootstrapped resources
line up with what validators check.

```http
GET  /api/host/events/{event_id}/resources          # health: namespace, per-team targets, registry
POST /api/host/events/{event_id}/resources/plan      # dry-run: { action: "bootstrap"|"reset" } → plan + blockers
POST /api/host/events/{event_id}/resources/bootstrap # CREATE CATALOG/SCHEMA (+ seed) per team
POST /api/host/events/{event_id}/resources/reset     # DROP SCHEMA ... CASCADE per team — needs { confirm: true }
```

`GET .../resources` →

```json
{
  "namespace": { "catalog": "quest_ai_bi_day", "schema_prefix": "team_" },
  "namespace_error": null,
  "targets": [ { "team_id": "team_red", "team_name": "Red", "catalog": "quest_ai_bi_day", "schema": "team_red", "fqn": "quest_ai_bi_day.team_red" } ],
  "resources": [ { "resource_id": "res_…", "team_id": "team_red", "resource_type": "schema", "fqn": "quest_ai_bi_day.team_red", "status": "active", "message": null } ],
  "warehouse_configured": true
}
```

`POST .../resources/plan` returns the ordered statements without executing:

```json
{
  "action": "bootstrap",
  "plan": [ { "op": "create_schema", "team_id": "team_red", "resource_type": "schema", "target": "quest_ai_bi_day.team_red", "sql": "CREATE SCHEMA IF NOT EXISTS quest_ai_bi_day.team_red", "within_namespace": true } ],
  "blockers": [],
  "warehouse_configured": true
}
```

**Behaviour:** `bootstrap`/`reset` require a SQL warehouse
(`QUEST_SQL_WAREHOUSE_ID`) and return `503 NO_WAREHOUSE` otherwise; `plan` works
without one (dry-run). `reset` requires `confirm: true` (`400 CONFIRM_REQUIRED`
otherwise) and **refuses the whole plan** with `409 OUTSIDE_NAMESPACE` if any
target falls outside the namespace — nothing is dropped. Bootstrap is idempotent
(`CREATE ... IF NOT EXISTS`). Every action is written to `event_audit_log`
(including `resources.reset.refused`), and resource health is tracked in
`event_resources`.

### Event report & export (PR11)

Post-event artifacts for enablement and account follow-up. Both endpoints are
host-only (`require_host`) and read-only; every repo call degrades to empty so a
partially-available Lakebase still yields a (smaller) report rather than a 500.

```http
GET /api/host/events/{event_id}/report                 # structured JSON (for the UI panel)
GET /api/host/events/{event_id}/export?format=json|csv|markdown   # downloadable artifact
```

`format` defaults to `json`; an unknown value returns `400 BAD_FORMAT`. Each
export sets `Content-Disposition: attachment; filename="<slug>-report.<ext>"` and
is written to `event_audit_log` as `report.export` (with the chosen format).

The report (and the `report` JSON endpoint body) contains:

```json
{
  "summary": { "event_id": "evt_1", "slug": "ai-bi-day", "title": "AI/BI GameDay",
               "status": "completed", "participants": 24, "teams": 6,
               "quests": 3, "tasks": 6, "attempts": 142,
               "attempts_by_status": { "passed": 120, "failed": 22 } },
  "leaderboard": [ { "rank": 1, "team_id": "team_red", "team_name": "Red Team", "total_points": 1250 } ],
  "teams": [ { "team_id": "team_red", "team_name": "Red Team", "members": 4 } ],
  "completion_matrix": [ { "team_id": "team_red", "team_name": "Red Team",
                           "completed_count": 6, "total_tasks": 6, "completion_pct": 100.0,
                           "completed": ["t1","t2","t3","t4","t5","t6"] } ],
  "task_catalog": [ { "task_id": "t1", "task_title": "First query", "quest_title": "Warm up", "points": 20 } ],
  "validation_failures": [ { "task_id": "t6", "task_title": "Dashboard", "status": "failed", "attempts": 8 } ],
  "hint_usage": [ { "team_id": "team_blue", "team_name": "Blue Team", "task_title": "Dashboard", "hint_id": "h1", "penalty": -10 } ],
  "hint_total_penalty": -30,
  "blockers": [ { "task_id": "t6", "task_title": "Dashboard", "quest_title": "Dashboards",
                  "solved_teams": 2, "total_teams": 6, "failed_attempts": 8 } ],
  "champions": [ { "rank": 1, "team_id": "team_red", "team_name": "Red Team", "total_points": 1250 } ],
  "fastest_team": { "team_id": "team_red", "team_name": "Red Team", "first_solves": 5 },
  "recommended_follow_ups": [ "Reinforce 'Dashboard' (Dashboards): only 2/6 teams completed it — …" ]
}
```

- **Blockers** are tasks a minority of teams solved or that drew failed/errored
  attempts, ordered hardest-first (fewest solves, then most failures).
- **Champions** are the top 3 ranked teams; **fastest_team** is the team with the
  most first-solves (first team to complete each task).
- **Recommended follow-ups** are derived heuristics for the account/enablement
  motion (reinforce blocker tasks, review hint-heavy tasks for doc/product gaps,
  1:1s for low-completion teams, recognise champions). When no blockers, weak
  teams, or hint usage are detected, a "smooth event — try a harder pack" signal
  is emitted instead.
- **CSV** is team-centric: `rank, team, points, tasks_completed, total_tasks,
  completion_pct, hints_used`, then one `0/1` column per task. Cells beginning
  with `=`, `+`, `-`, or `@` are prefixed with `'` to neutralise spreadsheet
  formula injection.
- **Markdown** renders summary, leaderboard, champions, completion, blockers,
  hint usage, and follow-ups as a readable leave-behind.

## Multi-workspace federation endpoints (ADR_006)

These endpoints support the shared-Lakebase multi-workspace mode. They are
additive — a standalone deploy still exposes them, but `/api/federation/status`
reports `role: "standalone"` and the host endpoints are unused. See
`adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md`.

`/api/health` additionally returns a `federation` block (`role`, `workspace_id`,
`event_slug`) so operators can confirm a deploy's role at a glance. The fields
are additive and safe for existing clients to ignore.

### Federation status (any role)

```http
GET /api/federation/status
```

Returns this deploy's role and — for a child — whether the current lab user is
mapped to a team yet, plus a DB-connection health flag for the UI indicator:

```json
{
  "role": "child",
  "workspace_id": "ws-anzgt-01",
  "event_slug": "anz-gameday",
  "event_id": "evt_123",
  "submitted_by": "labuser+1@awsbricks.com",
  "mapped": true,
  "db_connected": true,
  "team": { "team_id": "team_1", "display_name": "Red Team" }
}
```

### Event-wide leaderboard (child read)

```http
GET /api/federation/leaderboard?event={optional slug or id}
```

Reads the shared `event_leaderboard` view (spans every workspace) and highlights
this workspace's own team:

```json
{
  "leaderboard": [
    { "event_id": "evt_123", "team_id": "team_1", "display_name": "Red Team",
      "total_points": 1250, "rank": 1, "last_scored_at": "2026-06-10T10:05:00Z" }
  ],
  "you": { "team_id": "team_1", "display_name": "Red Team", "total_points": 1250, "rank": 1 },
  "mapped": true,
  "event_id": "evt_123",
  "workspace_id": "ws-anzgt-01"
}
```

When this workspace's lab user is not yet on the roster, `mapped` is `false` and
`you` is `null` — the child UI shows a graceful "not yet mapped" state while the
player keeps earning (unattributed) points.

### Import roster (master host)

```http
POST /api/host/events/{event_id}/roster/import
```

Request — a CSV mapping each lab workspace/user to a real person and team.
Columns: `workspace_id` (or `workspace_host`), `lab_user_email`, `team_name`,
optional `display_name`, `real_email`:

```json
{ "csv": "workspace_id,lab_user_email,display_name,real_email,team_name\nws-anzgt-01,labuser+1@awsbricks.com,Ada Lovelace,ada@corp.com,Red Team" }
```

Response (idempotent — re-import is safe and re-attributes previously unmapped
scores):

```json
{
  "event_id": "evt_123",
  "rows": 24,
  "teams_created": 6,
  "participants_created": 24,
  "identities_mapped": 24,
  "status": "imported"
}
```

### Workspace health (master host)

```http
GET /api/host/events/{event_id}/workspaces
```

Returns one row per checked-in child with write counts and last-seen time for
the host console health panel.

### Unmapped identities (master host)

```http
GET /api/host/events/{event_id}/identities/unmapped
```

Returns federated `(workspace_id, lab_user_email)` pairs writing scores that are
not on the roster yet, with their unattributed point totals — the host
reconciliation worklist.

## Error model

All API errors should return:

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Player-safe error message.",
    "request_id": "req_123"
  }
}
```

Host endpoints may include additional diagnostic detail.

## Auth and roles

Resolve user identity from Databricks App forwarded headers as the current app does, then map to event role.

Roles:

- `player`
- `team_captain`
- `host`
- `admin`

Permission examples:

| Action | Player | Captain | Host | Admin |
|---|---:|---:|---:|---:|
| View event | yes | yes | yes | yes |
| Join team | yes | yes | yes | yes |
| Submit attempt | yes | yes | yes | yes |
| Take hint | yes | yes | yes | yes |
| Create event | no | no | yes | yes |
| Import quest pack | no | no | yes | yes |
| Start/freeze event | no | no | yes | yes |
| Manual adjust score | no | no | yes | yes |
| Reset resources | no | no | yes | yes |

## Compatibility note

Do not break existing frontend while adding these APIs. Add the new APIs and route new pages gradually.
