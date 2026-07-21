# 13 — PR-Aligned Sprint Plan

> This is the **plan of record** (intended sequence). For **live status** — what
> has actually landed, what's deployable/testable, and known gaps — see
> [`STATUS.md`](STATUS.md).

## Delivery model

Each sprint should be a logical PR that can be reviewed independently. The build should avoid one massive branch.

Recommended cadence:

- 1 PR = 1 coherent product capability
- each PR includes tests or testable manual acceptance steps
- preserve existing adoption mode at every step
- deployable after every PR where possible

## Sprint / PR overview

| PR | Sprint | Core outcome |
|---:|---|---|
| PR01 | Domain model and migrations | Lakebase GameDay tables and backend repositories |
| PR02 | Configurable quest packs | manifest schema, loader, linter, import/list APIs |
| PR03 | Validation engine core | validator abstraction, SQL validator, manual validator |
| PR04 | Event and team management | create events, teams, participants, join flow |
| PR05 | Player gameplay experience | event lobby, team dashboard, quest runner, submit attempts |
| PR06 | Admin host console | host event controls, validation monitor, announcements |
| PR07 | Live scoring and leaderboard | scoring events, real-time leaderboard, freeze semantics |
| PR08 | Resource bootstrap and reset | team schema/data setup, dry-run, cleanup primitives |
| PR09 | Sample GameDay packs | AI/BI and Lakehouse sample quest packs with validators |
| PR10 | Security, observability, audit | role checks, audit log, metrics, validation safety controls |
| PR11 | Field reporting and hunter signaling | post-event report, CSV/Markdown export, sales signal summary |
| PR12 | Hardening, release, docs | test coverage, load testing, release guide, content authoring guide |
| PR13 | Federation foundation | `QUEST_ROLE` seam, writer-credential branch, migration 002, deploy role flags |
| PR14 | Child wiring + writer credential | federated write stamping, deterministic idempotency, startup check-in, master event-writer role |
| PR15 | Roster + identity reconciliation | roster CSV import, unmapped-identities + workspace-health host endpoints |
| PR16 | Federation UX | child event leaderboard + own-team rank, master host console panels |

PR13–PR16 deliver the shared-Lakebase multi-workspace federation
(`adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md`). They are strictly
additive: standalone mode is the unchanged default at every step, and one
codebase/build serves the `standalone`, `master`, and `child` roles purely via
runtime parameters.

---

## PR01 — Domain model and migrations

### Goal

Add the schema foundation for GameDay mode without changing the current user-facing app.

### Scope

- Add migration runner.
- Add Lakebase tables for quest packs, events, teams, participants, attempts, validators, scoring events, hints, announcements, audit.
- Add repository classes.
- Add basic health endpoint showing migration status.

### Key files

- `app/db.py`
- `app/migrations/*`
- `app/repositories/*`
- `deploy.sh`

### Acceptance criteria

- Existing app still starts.
- Migration runner is idempotent.
- New tables exist after deploy.
- `/api/health` includes schema status.

---

## PR02 — Configurable quest packs

### Goal

Move toward content-as-config.

### Scope

- Define quest pack manifest schema.
- Add loader/linter.
- Add import/list/detail APIs.
- Add sample manifest.
- Add server-side validation errors.

### Acceptance criteria

- Host can import a sample quest pack.
- Manifest errors are human-readable.
- Quest pack versions are immutable once published.

---

## PR03 — Validation engine core

### Goal

Create the core validator abstraction and first validators.

### Scope

- Add `Validator` interface.
- Add SQL assertion validator.
- Add manual validator.
- Add validation result normalization.
- Add attempt submission endpoint.
- Add scoring event idempotency.

### Acceptance criteria

- Submitting a task creates an attempt.
- SQL validator can pass/fail.
- Manual validator can be host-approved.
- Scoring only awards base points once.

---

## PR04 — Event and team management

### Goal

Make events and teams first-class.

### Scope

- Create event APIs.
- Participant import/join flow.
- Team creation and assignment.
- Event lifecycle statuses.
- Role checks.

### Acceptance criteria

- Host can create an event from a quest pack.
- Participants can join/view event.
- Teams can be assigned.
- Event status gates player actions.

---

## PR05 — Player gameplay experience

### Goal

Add the frontend experience for players.

### Scope

- Event lobby.
- Team dashboard.
- Quest list/detail.
- Attempt submission UI.
- Validation status UI.
- Hint drawer.

### Acceptance criteria

- Player can enter active event.
- Player can view team status.
- Player can submit a validation attempt.
- Pass/fail state displays clearly.

---

## PR06 — Admin host console

### Goal

Hosts can run an event.

### Scope

- Host dashboard.
- Start/pause/freeze/complete controls.
- Validation queue.
- Failed attempts.
- Announcements.
- Manual score adjustments.

### Acceptance criteria

- Host can control event lifecycle.
- Host can view validation failures.
- Host can send announcements.
- Host can adjust score with audit reason.

---

## PR07 — Live scoring and leaderboard

### Goal

Make competition reliable and exciting.

### Scope

- Team leaderboard API.
- Leaderboard materialized view or query.
- Recent scoring events.
- Frozen final leaderboard.
- Frontend live refresh.

### Acceptance criteria

- Leaderboard updates after passed validation.
- Hint penalties update score.
- Manual adjustments update score.
- Freeze blocks new submissions and finalizes rankings.

---

## PR08 — Resource bootstrap and reset

### Goal

Make event operations repeatable.

### Scope

- Team schema creation.
- Seed data loader.
- Resource dry-run.
- Reset/cleanup endpoint.
- Safe namespace checks.

### Acceptance criteria

- Host can bootstrap resources for all teams.
- Validators can reference team variables.
- Reset does not affect resources outside event namespace.

---

## PR09 — Sample GameDay packs

### Goal

Prove content model with useful field assets.

### Scope

- AI/BI GameDay pack.
- Lakehouse Foundations pack.
- At least 5 quests and 8 tasks.
- SQL and SDK validators.
- Facilitator notes.

### Acceptance criteria

- Packs import cleanly.
- Dry-run passes for expected baseline.
- A player can complete sample pack end-to-end.

---

## PR10 — Security, observability, audit

### Goal

Make the product trustworthy.

### Scope

- API role checks.
- SQL validator safety controls.
- Audit log writes.
- Metrics/logging.
- Error model hardening.

### Acceptance criteria

- Player cannot access host endpoints.
- Destructive SQL is blocked by default.
- Audit log records event lifecycle and scoring actions.
- Validation errors are safe for players.

---

## PR11 — Field reporting and hunter signaling

### Goal

Turn events into field follow-up artifacts.

### Scope

- Event report endpoint.
- Markdown/CSV export.
- Skill coverage summary.
- Completion heatmap.
- Sales signal summary.

### Acceptance criteria

- Host can export post-event report.
- Report includes top blockers, completions, hints, and champions.
- Output is usable in account follow-up.

---

## PR12 — Hardening, release, docs

### Goal

Make it field-ready.

### Scope

- E2E test path.
- Load test guidance.
- Docs.
- Authoring guide.
- Troubleshooting guide.
- Release checklist.

### Acceptance criteria

- Fresh deploy works.
- Existing adoption mode works.
- GameDay MVP works.
- Docs are sufficient for a field team to run an event.

---

## PR13 — Federation foundation

### Goal

Add the single-codebase seam for multi-workspace mode without changing
standalone behaviour.

### Scope

- `QUEST_ROLE` (standalone|master|child) runtime switch read once at startup
  (`app/config.py`), plus the federation env (`QUEST_WORKSPACE_ID`,
  `QUEST_EVENT_SLUG`).
- `app/db.py` explicit writer-credential branch used when `role=child`.
- `app/migrations/002_federation.sql`: nullable `workspace_id` on the fact
  tables, `event_workspaces`, `participant_identity_map`, and identity-resolving
  `team_scores` / `event_leaderboard` / `unmapped_identities` views.
- `deploy.sh` role flags (`--role`, `--master-lakebase-host/-token`, `--event`,
  `--workspace-id`); child skips local Lakebase + migrations.
- ADR_006 + docs/05, docs/07 updates.

### Acceptance criteria

- Standalone deploy and adoption mode are byte-for-byte unchanged in behaviour.
- Migration 002 is idempotent and backward compatible (re-runs cleanly).
- `event_leaderboard` keeps the standalone `team_id` path via `COALESCE`.

---

## PR14 — Child wiring and writer credential

### Goal

Let a child workspace write to the master Lakebase safely.

### Scope

- Child stamps federated writes with `workspace_id` + `submitted_by` and a
  deterministic `scoring_events.idempotency_key` (per workspace + source).
- One-shot `event_workspaces` check-in (DB upsert) on child startup — no outbox,
  no ingest API.
- Master provisions the shared INSERT-only event-writer Postgres role (grants on
  the four fact tables + `SELECT` on read tables/views) and surfaces the
  credential for distribution.
- Connectivity spike (`scripts/federation_spike.py`) verifying reachability,
  auth, and grant scope.

### Acceptance criteria

- A child write appears once in the shared DB; a retry never double-awards.
- The event-writer role can `INSERT`/`SELECT` only — `UPDATE`/`DELETE`/DDL fail.
- A child checks itself into `event_workspaces` on startup.

---

## PR15 — Roster and identity reconciliation

### Goal

Turn generic `labuser+{n}@awsbricks.com` lab users into named teams.

### Scope

- `POST /api/host/events/{event_id}/roster/import` (CSV) pre-creates
  teams/participants and populates `participant_identity_map`; re-import is
  idempotent.
- `GET /api/host/events/{event_id}/identities/unmapped` reconciliation worklist.
- `GET /api/host/events/{event_id}/workspaces` per-workspace health.

### Acceptance criteria

- Roster import maps identities; re-import re-attributes previously unmapped
  scores without duplicating teams/participants.
- Unmapped identities are surfaced with their unattributed point totals.

---

## PR16 — Federation UX

### Goal

Give children event-wide visibility and hosts a federation console.

### Scope

- Child UI: event-wide leaderboard from `event_leaderboard` with this
  workspace's own team + rank highlighted; graceful "not yet mapped" state;
  DB-connection health indicator.
- Master UI: workspace-health panel, roster import, and unmapped-identities
  screen.
- Role-aware navigation (one build; nav adapts to `role`).

### Acceptance criteria

- A child shows global standings and its own team's rank from inside its
  workspace.
- An unmapped child shows the "not yet mapped" state and keeps playing.
- The master console shows workspace health and reconciliation.
