# Databricks Quest — GameDay Mode

> **Living document.** GameDay (Event Mode) is being built PR by PR. This README
> tracks how to deploy and operate it **as features land**, and marks what works
> today vs. what's coming. The adoption-mode product is documented in
> [`README.md`](README.md); the deep design lives in [`docs/`](docs/).

Databricks Quest runs in two modes from **one codebase and one build**:

- **Adoption Mode** (default) — the system-table-driven platform-adoption game.
  Unchanged; see [`README.md`](README.md).
- **Event Mode (GameDay)** — configurable, quest-pack-driven, team-based events
  with validation, scoring, leaderboards, host controls, and (optionally)
  many workspaces federating into one.

Nothing here breaks Adoption Mode. **Event Mode is opt-in and off by default** —
a deploy with no GameDay flags is the legacy adoption app, byte-for-byte: the
GameDay APIs return `404`, the Event UI is hidden, and the GameDay schema
migrations are skipped. Enable GameDay explicitly with `--event-mode` (or set
`QUEST_EVENT_MODE=on`); the `master`/`child` roles imply it.

---

## Build status

**Live status lives in one place:
[`docs/STATUS.md`](docs/STATUS.md)** — the authoritative per-PR tracker (what's
landed, what's deployable/testable, known gaps). In short: PR01–PR12 and the
federation plumbing (PR13–PR16) have landed — schema, quest packs, the
validation/scoring write path, event/team lifecycle, the player gameplay UI, the
host console, the live player leaderboard with hint-penalty scoring,
namespace-guarded team resource bootstrap/reset, two shipped sample quest packs,
security/observability hardening (request ids, structured logs, expanded
health, permission-model docs), the post-event report with JSON/CSV/Markdown
export for account follow-up, and release hardening (dual-mode README plus
troubleshooting, release-checklist, and manual-E2E/load-test guides). The GameDay
MVP is field-ready.

> **What this means for testing:** the full loop is testable now — a host can
> create an event, import a pack, run the lifecycle, players join and submit
> attempts, validators score them, and the host monitors attempts, posts
> announcements, and adjusts scores from the console. See [Testing](#testing).

---

## Deployment switches

Everything is selected by runtime parameters (env vars set from `deploy.sh`
flags). There are **two orthogonal switches**:

1. **`QUEST_EVENT_MODE`** (`--event-mode`) — the GameDay master switch, **off by
   default**. Off = legacy adoption app (GameDay APIs `404`, Event UI hidden,
   GameDay migrations skipped). On = GameDay surfaces activate. Implied by the
   `master`/`child` roles.
2. **`QUEST_ROLE`** — federation topology, only meaningful once Event Mode is on:

| Role | What it is | Lakebase | GameDay migrations | Scoring pipeline |
|---|---|---|---|---|
| `standalone` (default) | Single-workspace app | provisions its own local | only if `--event-mode` | runs (adoption) |
| `master` | Owns the shared event DB + host console (implies Event Mode) | provisions its own | runs (incl. `002`) | runs (adoption) |
| `child` | One per attendee; writes to the master's DB (implies Event Mode) | **points at master** | **skipped** | **skipped** |

See [`adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md`](adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md)
for why children connect directly to the master's Lakebase over Postgres
(instead of HTTP between apps).

> **Event Mode requires the Lakebase backend.** The optional SQL warehouse data
> backend (`--data-backend warehouse`, toggled under Admin -> Data Backend) is
> **read-only** — it serves adoption reads directly from the scored Delta tables.
> GameDay writes (team bootstrap, attempts, live scoring) need Lakebase, so keep
> the active backend on **Lakebase** when running an event.

### Admin page access (`--admins`)

The **Admin** page (`/api/admin/*`) is gated by an allowlist, independent of
Event Mode and applies to **every deploy**, including the legacy adoption app.

Admins are stored in **Lakebase** (the `quest_admins` table), which is the
shared source of truth. In federation the master owns it and child apps read it
through the shared event-writer credential — so **an admin is automatically an
admin across the standalone app, the master, and every child workspace**. The
effective admin set is the union of that table and the deploy-time env
allowlist (the bootstrap/fallback).

Seed the initial admins with `--admins`:

```bash
./deploy.sh --admins "alice@corp.com,bob@corp.com"
```

Behaviour:

- **`--admins` provided** → those emails are seeded into `quest_admins`; only
  admins see the Admin page (others get `403` and the nav item is hidden).
- **`--admins` omitted** → `deploy.sh` defaults to the **deploying user** (so
  the page is never open to everyone) — **except `--role child`**, which seeds
  nothing and inherits the admin list from the master's shared DB.

**Admins manage admins in-app.** The Admin page has an *Admin Access* card to
add/remove admins at runtime (or use the API directly):

```bash
# List admins
curl -s "$APP/api/admin/admins"
# Add an admin (any admin can grant admin; works from master or a child app)
curl -s -X POST "$APP/api/admin/admins" -H 'Content-Type: application/json' \
  -d '{"email":"carol@corp.com"}'
# Remove an admin — from the MASTER/standalone app only (child role has no
# DELETE on the shared table; last-admin removal is refused to avoid lockout)
curl -s -X DELETE "$APP/api/admin/admins/carol@corp.com"
```

Mechanics: `--admins` sets `QUEST_ADMIN_ALLOWLIST` in `app.yaml` (the seed +
fallback), and `/api/profile` returns `is_admin` so the frontend hides the nav.
The page is "open to all" only when nothing is configured anywhere — no env
allowlist **and** an empty/unreachable `quest_admins` (e.g. running locally
without `deploy.sh`).

### Host access (`--host-allowlist`) — fail-closed in Event Mode

The **Host console** (`/api/host/*`) is GameDay-only and gated separately from
the Admin page. A caller is a host when they are in `QUEST_HOST_ALLOWLIST`, are
an admin, **or** are listed in the event's `event_hosts` rows (managed in-app via
`/api/host/events/{id}/hosts`).

**The gate is fail-closed.** In Event Mode, if no host authority is configured
anywhere — empty allowlist, no admins, and no `event_hosts` for the event —
access is **denied** (not open). The only escape hatch is the dev-only
`QUEST_HOST_OPEN=1`, intended for local testing.

Seed the allowlist at deploy time:

```bash
./deploy.sh --event-mode --host-allowlist "host1@corp.com,host2@corp.com"
```

Any `--event-mode` / `--role master` deploy requires either `--host-allowlist`
or `--admins` (defaulting to the deploying user, as before) and the deploy
script prints the **effective host authority** loudly so you can't ship an event
nobody can host. `--host-allowlist` writes `QUEST_HOST_ALLOWLIST` into
`app.yaml`. The frontend Host tab keys off the same rule (`lobby.is_host`), so
"see the tab → 403" and "allowlisted → no tab" can't happen.

---

## Deploy — single workspace

**Legacy adoption app (default).** No GameDay surface at all:

```bash
./deploy.sh
```

`/api/health` reports `"event_mode": false`; `/api/events/*`, `/api/host/*`, and
`/api/federation/*` return `404`.

**Single-workspace GameDay.** For a small event run entirely in one workspace,
add `--event-mode`:

```bash
./deploy.sh --event-mode
```

`/api/health` then reports `"event_mode": true` and migrations `001_gameday_core`
+ `002_federation` applied. You can then import a quest pack (below).

---

## Deploy — multi-workspace federation (large lab events)

One **master** app on the admin workspace owns the shared Lakebase; each attendee
workspace runs a **child** app that writes to it.

### 1. Deploy the master

```bash
./deploy.sh --role master --event anz-gameday
```

The master provisions its Lakebase, runs migrations, and **creates the shared
INSERT-only event-writer role** (`quest_event_writer`). It generates a random
secret for that role and prints a ready-to-copy child command in the success
banner — this is where the token comes from (see below).

### Where the event-writer token comes from

You do not invent or fetch the token separately — the **master deploy mints it
for you**. `deploy.sh --role master`:

1. generates a random secret (`python3 -c "import secrets; print(secrets.token_urlsafe(24))"`),
2. creates/updates the `quest_event_writer` Postgres role on the master Lakebase
   with that secret (INSERT on the fact tables + SELECT on the read views), and
3. prints it once in the success banner:

```
── Shared event-writer credential (give to child workspaces) ──

  Children deploy with these flags (rotate per event):

    ./deploy.sh \
      --role child \
      --event anz-gameday \
      --master-lakebase-host <master-host> \
      --master-lakebase-user quest_event_writer \
      --master-lakebase-token '<generated-secret>'
```

The `<generated-secret>` in that banner **is** the
`--master-lakebase-token <event-writer-credential>` you pass to children.

Important:
- **Shown once, not persisted.** The script does not store the secret. Copy it
  into your secret store immediately.
- **Lost it? Re-run the master deploy.** Provisioning does `ALTER ROLE ...
  PASSWORD` on re-run, so it rotates the secret — then redistribute the new one.
- **It's a Postgres role password, not an OAuth token.** Child workspace OAuth
  identities aren't accepted by the master's Lakebase, which is why a shared
  credential is used (see ADR_006). Treat it as **event-scoped and rotate per
  event**.

### 2. Deploy a child into each attendee workspace

Prefer passing the token via an environment variable / secret rather than inline,
so it doesn't land in shell history or process listings:

```bash
export QUEST_WRITER_TOKEN='<generated-secret>'   # from the master banner / your secret store

./deploy.sh --role child \
  --master-lakebase-host <master-lakebase-host> \
  --master-lakebase-user quest_event_writer \
  --master-lakebase-token "$QUEST_WRITER_TOKEN" \
  --event anz-gameday \
  --workspace-id ws-anzgt-01
```

Notes:
- Setting `--master-lakebase-host` alone defaults `--role` to `child`.
- `--master-lakebase-user` defaults to `quest_event_writer`; only pass it if you
  changed the role name on the master.
- The child **skips local Lakebase provisioning, migrations, and the scoring
  pipeline** — it only writes event facts to the shared DB.
- `--workspace-id` is how federated writes are attributed; it defaults to the
  workspace host if omitted. Give each attendee workspace a unique value.
- The child checks itself into `event_workspaces` on startup (a DB upsert, not an
  HTTP call), so it appears in the master's Workspaces health panel.
- **Verify before the event** — confirm reachability and that the credential is
  correctly INSERT-only:

  ```bash
  PGPASSWORD="$QUEST_WRITER_TOKEN" python3 scripts/federation_spike.py \
    --host <master-lakebase-host> --db quest_db --user quest_event_writer
  ```

### Full deploy flag reference

```bash
./deploy.sh --help
```

---

## Quest packs (works today)

Quests are configuration, not code. The single guided walkthrough is
[`docs/AUTHORING_QUEST_PACKS.md`](docs/AUTHORING_QUEST_PACKS.md) (scaffold →
author → lint → import → version-bump); the deep-dive references are
[`samples/QUEST_PACK_SCHEMA.md`](samples/QUEST_PACK_SCHEMA.md),
[`samples/SAMPLE_VALIDATOR_LIBRARY.md`](samples/SAMPLE_VALIDATOR_LIBRARY.md), and
[`samples/SAMPLE_QUEST_PACK_AI_BI.md`](samples/SAMPLE_QUEST_PACK_AI_BI.md). A
committed Cursor skill (`.cursor/skills/quest-pack-author/`) drives both the
create and update flows for agents.

**Validator types.** `sql_assertion` (read-only `SELECT`/`WITH` against the
team's bootstrapped namespace) and `databricks_sdk`/`workspace_api` (read-only
workspace lookups — `dashboard_exists_for_team`, `table_exists`,
`genie_space_exists`, `job_exists_with_schedule`, …) **execute** today;
`manual` routes to host review. The live, executable set is reported by
`GET /api/health` (`validator_types` + `sdk_checks`) so authors never claim a
check runs when it is actually host-reviewed. Always pair every `databricks_sdk`
task with a `manual` fallback so a pilot is never blocked when a check can't run.

Lint locally without a server:

```bash
python scripts/lint_quest_pack.py quest_packs/built_in/ai_bi_gameday.yml
```

Lint / import / list against a running app (authenticated session or forwarded
identity headers):

```bash
# Lint
curl -sX POST "$APP_URL/api/host/quest-packs/lint" \
  -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json; print(json.dumps({"manifest_yaml": open("quest_packs/built_in/ai_bi_gameday.yml").read()}))')"

# Import (immutable per (slug, version) — bump the version to change content)
curl -sX POST "$APP_URL/api/host/quest-packs/import" \
  -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json; print(json.dumps({"manifest_yaml": open("quest_packs/built_in/ai_bi_gameday.yml").read()}))')"

# List
curl -s "$APP_URL/api/host/quest-packs"
```

---

## Run an event (works today — PR04)

Once a pack is imported, a host can run an event entirely over the API. Players
self-join; hosts can also place people on teams directly. Event status governs
play: **only an `active` event accepts attempts** (others return
`409 EVENT_NOT_ACTIVE`).

```bash
# 1. Create an event from an imported pack version (creator becomes owner-host)
curl -sX POST "$APP_URL/api/host/events" -H 'Content-Type: application/json' \
  --data '{"title":"ANZ GameDay","pack_version_id":"<packver_id>","slug":"anz-gameday"}'

# 2. Create teams
curl -sX POST "$APP_URL/api/host/events/anz-gameday/teams" \
  -H 'Content-Type: application/json' --data '{"name":"red","display_name":"Red Team"}'

# 3a. Bulk-import participants (teams created on demand, idempotent)
curl -sX POST "$APP_URL/api/host/events/anz-gameday/participants/import" \
  -H 'Content-Type: application/json' \
  --data '{"participants":[{"email":"ada@corp.com","team_name":"red"}]}'

# 3b. …or assign someone to a team directly (single team per event; reassigns)
curl -sX POST "$APP_URL/api/host/events/anz-gameday/teams/<team_id>/members" \
  -H 'Content-Type: application/json' --data '{"user_id":"ada@corp.com"}'

# 4. Open play, then players join + submit
curl -sX POST "$APP_URL/api/host/events/anz-gameday/start"
curl -sX POST "$APP_URL/api/events/anz-gameday/join" \
  -H 'Content-Type: application/json' --data '{"team_name":"red"}'

# 5. Lifecycle: pause | freeze | complete (and ready | archive)
curl -sX POST "$APP_URL/api/host/events/anz-gameday/freeze"
```

Lifecycle state machine: `draft → ready → active → paused/frozen → completed →
archived`. `start` stamps `starts_at`, `complete` stamps `ends_at`, `freeze`
stamps `scoring_frozen_at`. Players see joinable/active events at `GET /api/events`
and the lobby at `GET /api/events/{id}`. Every mutation writes an
`event_audit_log` row.

---

## Player experience (works today — PR05)

When Event Mode is on, the sidebar shows an **Event** tab (the player UI). It
sits on the read endpoints below and the PR03 attempt endpoint — no API calls
needed by hand:

- **Lobby** — pick an active event (`GET /api/events`), see its teams and
  counts (`GET /api/events/{id}`), set a display name, and join a team
  (`POST /api/events/{id}/join`).
- **Quests** — the event pack's quests with your team's progress bar
  (`GET /api/events/{id}/quests`).
- **Quest runner** — open a quest (`GET /api/events/{id}/quests/{quest_id}`)
  to read the narrative, work each task, reveal hints, and submit a JSON
  `submission`. The submit button calls
  `POST /api/events/{id}/tasks/{task_id}/attempts` and shows a live
  pass/fail/manual/error badge plus the player-safe per-validator messages and
  points awarded. Submissions are disabled unless the event is `active` and you
  have joined.
- **Team dashboard** — score, rank, members, task progress, and recent scoring
  (`GET /api/events/{id}/team`).

Every event view now has a **Standings** tab (PR07, see below). Role behaviour:
`master` deployments show the **Host Console** instead of the player UI; `child`
deployments get the player UI and their Standings tab renders the event-wide
*federated* leaderboard (own-team rank highlighted). A `child` app is pinned to
its configured event; a `standalone` app lets the player choose from the event
list.

All players also see a **host announcement banner** at the top of the event
view (`GET /api/events/{id}/announcements`).

---

## Host console (works today — PR06)

Facilitators run the event from a **Host** tab that appears inside the Event
view only for hosts (`is_host` on the lobby; backed by `QUEST_HOST_ALLOWLIST`).
Players never see it. It's gated by `require_host`, so a non-host gets a 403 on
the underlying endpoints even if they craft the URL.

What the host can do from the console (`GET /api/host/events/{id}` drives the
dashboard):

- **Lifecycle controls** — start / pause / freeze / complete / mark-ready /
  archive. Only the legal next transitions render (`allowed_transitions`),
  enforcing the PR04 state machine. Submissions are open only while `active`.
- **Teams & scores** — ranked table of teams with points and member counts
  (`GET /api/host/events/{id}/teams`).
- **Validation attempts inspector** — filter by `all/passed/failed/error/manual/
  running` (`GET /api/host/events/{id}/attempts?status=`) and expand any attempt
  to see per-validator results **including the private diagnostics**
  (`GET /api/host/events/{id}/attempts/{attempt_id}`) that players never see.
- **Announcement composer** — post `info/warning/critical` broadcasts
  (`POST /api/host/events/{id}/announcements`); players see them in the banner.
- **Manual score adjustment** — add or subtract points for a team with a
  **required reason** (`POST /api/host/events/{id}/adjustments`). This writes a
  `manual_adjustments` audit row and a matching `scoring_events` ledger row in
  one transaction, so the leaderboard updates immediately. Every action is
  written to `event_audit_log`.
- **Quest pack import / lint** — paste a manifest, lint it, then import a new
  immutable version (`POST /api/host/quest-packs/lint` and `/import`, PR02).

> Note on the manual-adjustment ledger row: it carries a unique idempotency key
> (the adjustment id), so unlike base-points awards it is never deduplicated — a
> manual override is intentional and always lands.

---

## Live leaderboard & hint penalties (works today — PR07)

Every event has a **Standings** tab (all roles). For standalone/master it calls
`GET /api/events/{id}/leaderboard` and renders:

- a **podium** (top 3) and a **ranked table of all teams**, with the caller's
  own team highlighted (and surfaced with a `null` rank when on a team but not
  yet scored);
- a **recent activity feed** — team + task names with signed point deltas,
  including task passes, **hint penalties**, and host manual adjustments;
- a **freeze / final badge** — when the event is `frozen`/`completed`/`archived`
  or has `scoring_frozen_at` set, the board shows "Scoring frozen" / "Final
  results" and no new player scoring lands.

**Ranking is deterministic.** The `event_leaderboard` view ranks by
`total_points DESC` then `last_scored_at ASC`, so ties go to the team that
reached the total first — the order never flickers between refreshes.

**The board is live.** The Standings tab polls every ~10s on top of the manual
Refresh and pauses while the browser tab is hidden, so spectators and players
see scores move without reloading. Player attempt views poll the attempt record
after submit so `queued`/`running` validations resolve in-place.

**Hint penalties now affect the score.** Hint bodies are withheld until a player
reveals them. Revealing calls
`POST /api/events/{id}/tasks/{task_id}/hints/{hint_id}/reveal`, which returns the
body and charges the penalty **once per team** (idempotent — re-revealing is
free). The penalty is normalised to a non-positive delta, so an author can write
`-10` (canonical) or `10` and it always subtracts. While the event is paused or
frozen the body is still shown but **no penalty is charged** (no new scoring when
play is closed).

**What updates a team's score (all land in the `scoring_events` ledger):**

| Source | `source_type` | Sign |
|---|---|---|
| Passing a task's validators | `validation` | `+points` (once per scope) |
| Revealing a hint | `hint_penalty` | `−penalty` (once per team) |
| Host manual adjustment | `manual_adjustment` | `±` (always lands) |

---

## Sample quest packs (works today — PR09)

Two built-in, ready-to-run packs live in [`samples/packs/`](samples/packs/) and
prove the platform end-to-end:

- **AI/BI GameDay** (`ai_bi_gameday.yml`) — govern a schema, build a trusted
  revenue model, ship an AI/BI dashboard + Genie space. 3 quests / 6 tasks.
- **Lakehouse Foundations** (`lakehouse_foundations.yml`) — the bronze→silver
  medallion loop with a provable quality gate. 3 quests / 6 tasks.

Each ships learning objectives, `resources.seed_sql` (per-team seed data that the
PR08 bootstrap runs), `quest_completed` unlock gating, `sql_assertion` +
`databricks_sdk` + `manual` validators, hints, and facilitator notes. Both lint
and import cleanly and are covered by `tests/test_sample_packs.py`.

Run flow and customization guide:
[`samples/packs/README.md`](samples/packs/README.md). In short: import the pack →
create an event + teams → bootstrap resources → start → play. The first quest
opens with a warehouse-independent `SELECT 1` check so teams can confirm their
warehouse binding before the timed quests.

---

## Post-event report & export (works today — PR11)

When an event wraps, the host gets a **Report** panel in the Host console (and two
API endpoints) that turn the event into an enablement/account leave-behind:

- **Structured report** — `GET /api/host/events/{event_id}/report` returns event
  summary, leaderboard, a team×task completion matrix, validation failures, hint
  usage, blockers (hardest tasks), champions/high performers, and **recommended
  follow-ups**. The follow-ups are heuristics for the field motion: reinforce the
  tasks most teams got stuck on, review hint-heavy tasks for doc/product gaps, set
  up 1:1s for low-completion teams, and recognise top performers as potential
  champions/references.
- **Export** — `GET /api/host/events/{event_id}/export?format=json|csv|markdown`
  downloads the report. **CSV** is team-centric (rank, points, completion %, hints
  used, plus a 0/1 column per task) and drops straight into a spreadsheet; cells
  are guarded against formula injection. **Markdown** is a readable recap.
  **JSON** is the full structured payload.

Every export is audited (`report.export`). The endpoints are host-only and
read-only, and degrade gracefully if Lakebase is only partially available.

---

## Team resource bootstrap & reset (works today — PR08)

The host can provision and tear down each team's Databricks resources from a
**Resources** panel in the Host console — repeatably and safely.

- **Per-team namespace** — each team gets `catalog.schema` computed by
  `services/namespace.py`: `catalog` = the team's `team_catalog`, or the event's
  `config_json.resource_namespace.catalog`, or the default `quest_<event-slug>`;
  `schema` = the team's `team_schema`, or `<schema_prefix><team-name>`. These are
  the same values the validators template (`${team_catalog}`/`${team_schema}`),
  so provisioned resources line up with what tasks check.
- **Dry-run first** — "Dry-run plan" (`POST .../resources/plan`) shows the exact
  `CREATE CATALOG/SCHEMA` (+ pack seed SQL) statements, flagging anything
  out-of-namespace, without running anything. No warehouse needed.
- **Bootstrap** — `POST .../resources/bootstrap` runs the plan on the configured
  SQL warehouse (`QUEST_SQL_WAREHOUSE_ID`), idempotently (`IF NOT EXISTS`), and
  seeds sample data from the pack's optional `resources.seed_sql`.
- **Reset** — `POST .../resources/reset` drops every team schema **only within
  the event's namespace**. It requires `confirm: true` and **refuses the whole
  plan** if any target is outside the namespace (a reserved catalog like
  `main`/`system`, a bare catalog, a wildcard, or another event's schema) —
  nothing is dropped. Every action (including a refusal) lands in
  `event_audit_log`; resource health is tracked in `event_resources`.

> Safety: the namespace guard is pure and unit-tested, and is the *sole*
> authority on what is in-namespace — an executor bug cannot widen it. A reset
> can never touch a schema this event did not compute.

Add a pack seed section like this to auto-create warm-up data per team:

```yaml
resources:
  seed_sql:
    - "CREATE TABLE IF NOT EXISTS ${team_catalog}.${team_schema}.warmup (id INT)"
```

---

## Federation operations (master host)

Once children are deployed and an event exists, the host works from the master:

- **Import roster** — map generic lab users to real people/teams. CSV columns:
  `workspace_id` (or `workspace_host`), `lab_user_email`, `team_name`, optional
  `display_name`, `real_email`. Re-import is idempotent and re-attributes
  previously unmapped scores.

  ```bash
  curl -sX POST "$MASTER_APP_URL/api/host/events/<event_id>/roster/import" \
    -H 'Content-Type: application/json' \
    --data '{"csv":"workspace_id,lab_user_email,display_name,team_name\nws-anzgt-01,labuser+1@awsbricks.com,Ada Lovelace,Red Team"}'
  ```

- **Workspace health** — `GET /api/host/events/<event_id>/workspaces`
- **Unmapped identities** — `GET /api/host/events/<event_id>/identities/unmapped`

The same panels are available in the master's **Host Console** UI; children see
the event-wide leaderboard and their own team's rank in their **Event** tab.

Full operator runbook:
[`docs/10_EVENT_OPERATIONS_PLAYBOOK.md`](docs/10_EVENT_OPERATIONS_PLAYBOOK.md).

---

## Testing

Tiered, because end-to-end gameplay depends on PR03 + PR04:

**Tier 0 — local, no Databricks (works now):**

```bash
cd frontend && npm install && npm run build
python -m compileall app notebooks
pytest tests/          # federation unit tests
```

**Tier 1 — infra, on deploy (works now):**
- Standalone/master/child boot; `/api/health` shows migrations `001` + `002`.
- Migration 002 is idempotent (re-run safe).
- **Connectivity + credential scope** — from a child, verify reachability, auth,
  and that the writer role is INSERT-only:

  ```bash
  python scripts/federation_spike.py --host <master-host> --db quest_db --user quest_event_writer
  ```

- Child startup check-in appears in the master Workspaces panel.

**Tier 2 — federation reads (needs an `events` row):**
- Roster import → teams/participants/identity map; `/api/federation/status`
  resolves a team; `unmapped_identities` and `event_leaderboard` views populate
  once a `scoring_events` row exists.
- Until PR04 adds event creation, seed an `events` row (and a sample
  `scoring_events` row) directly to exercise these.

**Tier 3 — full end-to-end (works now — PR03 + PR04):**
- Host creates event + teams, players join, a player completes a quest →
  validated → scored → appears on the master leaderboard → child sees its rank.
- Standalone: same flow in one workspace (`--event-mode`). Verified via the
  event-run API flow above; non-active statuses block attempts.

---

## Reference

- Architecture: [`docs/05_TARGET_ARCHITECTURE.md`](docs/05_TARGET_ARCHITECTURE.md)
- Data model: [`docs/07_DATA_MODEL.md`](docs/07_DATA_MODEL.md)
- API contract: [`docs/08_API_CONTRACT.md`](docs/08_API_CONTRACT.md)
- Quest model + validation engine: [`docs/06_QUEST_MODEL_AND_VALIDATION_ENGINE.md`](docs/06_QUEST_MODEL_AND_VALIDATION_ENGINE.md)
- PR sequence: [`docs/13_PR_ALIGNED_SPRINT_PLAN.md`](docs/13_PR_ALIGNED_SPRINT_PLAN.md)
- Federation decision: [`adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md`](adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md)
