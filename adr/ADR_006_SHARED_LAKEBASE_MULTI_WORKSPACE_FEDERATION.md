# ADR 006 — Shared-Lakebase Multi-Workspace GameDay Federation

## Status

Proposed (supersedes the "multi-workspace event federation" future note in
ADR_005 and `docs/05_TARGET_ARCHITECTURE.md`).

## Context

Large GameDay events provision **one Databricks workspace per attendee** (or
per team). Each attendee plays the quests in their own workspace, but the event
needs a single global leaderboard, one host console, and post-event reporting
in a central **master** workspace.

Two cross-workspace transports were considered:

1. **HTTP push** — each child app POSTs scoring facts to an ingest API on the
   master app, buffered through a local outbox + background pusher.
2. **Shared Lakebase** — the master workspace owns one Lakebase database; every
   child app points its database layer at that endpoint and writes directly.

HTTP push has to traverse the master workspace's **Databricks Apps auth proxy**
for every cross-workspace call. App-to-app auth across workspaces is the
riskiest, least-portable part of the design, and it forces us to build and
operate an ingest API, an outbox table, and a retry pusher.

Lakebase is Postgres on the wire (`:5432`, `sslmode=require`). A child
connecting to the master's Lakebase endpoint is a **database connection, not an
HTTPS call to the master app**, so it never hits the Apps auth proxy.
Aggregation is automatic because the existing `event_leaderboard` / `team_scores`
views already span all rows in the database.

A hard product constraint also applies: there is **one codebase and one build
artifact** for standalone, master, and child. Role is selected purely by
runtime parameters.

## Decision

Adopt the **shared-Lakebase** model.

- The **master** workspace owns one Lakebase database holding all event
  operational state. The master app uses it exactly as a standalone app does.
- Each **child** app sets `LAKEBASE_HOST` to the master endpoint and connects
  over Postgres using a **shared, INSERT-only event-writer credential**. Child
  apps run gameplay + validation locally and write `task_attempts`,
  `validation_results`, `scoring_events`, and `hints_taken` directly into the
  shared tables, stamped with `QUEST_WORKSPACE_ID` and `submitted_by`
  (`labuser+{n}@awsbricks.com`).
- A single **`QUEST_ROLE`** env var (`standalone` | `master` | `child`) is the
  only mode switch. Every endpoint and migration ships in every deployment;
  role only changes which paths are *activated* at runtime.
- **Identity is reconciled centrally.** Children are dumb about identity; the
  host uploads a roster CSV that maps `(workspace_id, lab_user_email)` to a real
  person and team in `participant_identity_map`. The leaderboard view resolves
  team membership through that map, so federated points attribute to the right
  team automatically. The standalone `team_id` path is preserved via `COALESCE`.
- **No federation_outbox, no ingest API, no pusher.** Postgres is the transport;
  connection retry covers transient blips.
- **Idempotency is unchanged.** Children set `scoring_events.idempotency_key`
  deterministically (`{workspace_id}:{event_id}:{task_id}:{scoring_rule}`); the
  existing `UNIQUE` constraint enforces single-award on retries.

## Consequences

### Positive

- Removes cross-workspace app auth — the single biggest risk — entirely.
- No new ingest API / outbox / pusher to build or operate.
- Aggregation is free: the leaderboard view already spans the whole DB.
- One codebase, one build; role and connection are pure parameters.
- Identity stays central, so children need zero per-event identity logic.

### Negative / trade-offs

- Children are **not autonomous** if the master Lakebase is unreachable.
  Managed/HA Lakebase makes this acceptable for a bounded event window.
- Hundreds of child apps → hundreds of connections on one Lakebase instance
  (the app caches ~1 connection/app for ~45 min). The instance must be sized
  accordingly and child connection use kept minimal.
- A shared writer credential is coarser than per-child OAuth roles. It is
  INSERT-only and rotated per event; per-child OAuth roles remain a future
  hardening option for stronger isolation/revocation.

## Implementation notes

- `db.py` gains an explicit writer-credential branch: when `LAKEBASE_USER` /
  `LAKEBASE_PASSWORD` (or `LAKEBASE_WRITER_TOKEN`) are set they override the
  workspace-OAuth path. Used when `QUEST_ROLE=child`.
- `app/migrations/002_federation.sql` (applied once to the shared master
  Lakebase): nullable `workspace_id` on the fact tables + `participants`; new
  `event_workspaces` and `participant_identity_map`; identity-resolving
  `team_scores` / `event_leaderboard`.
- `deploy.sh` master provisions the event-writer role + grants and prints its
  credential; child skips local Lakebase + migrations and points at master.
- Verify with `scripts/federation_spike.py` before every event: reachability,
  authentication, and that the writer is correctly INSERT-only.

## Security

- The event-writer role has `INSERT` on the four fact tables only, plus
  `SELECT` on the leaderboard read surface needed for the child UI, and
  `INSERT`/`UPDATE`/`SELECT` on `event_workspaces` for check-in. No
  `UPDATE`/`DELETE` on facts, no access to secrets.
- The credential is distributed as a deploy parameter and rotated per event.
- Children only ever write durable, non-sensitive facts; evidence summaries
  follow the same "no secrets in evidence" rule as standalone mode.
