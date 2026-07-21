# 03 — Codebase Deep Dive

Repository reviewed: `https://github.com/deepbasu123/databricks-quest`

## High-level architecture today

The repo currently implements a Databricks-native gamification app:

```text
React frontend
   ↓
FastAPI backend
   ↓
Lakebase PostgreSQL read model
   ↑
Delta scoring tables
   ↑
Scheduled scoring notebook over system tables
```

The README describes the architecture as a scoring pipeline that reads Databricks system tables, computes missions, syncs scored data to Lakebase, and serves a React + FastAPI Databricks App.

## Current strengths

### 1. Correct platform primitives

The repo already uses the right Databricks building blocks:

- Databricks Apps for the UI/API
- Databricks Asset Bundles for deployment
- Databricks Workflows for scheduled scoring
- Unity Catalog/Delta tables for scoring output
- Lakebase/PostgreSQL for low-latency reads
- React + TypeScript frontend
- FastAPI backend

This is a strong foundation. The GameDay platform should evolve it, not throw it away.

### 2. Simple deployment experience

`deploy.sh` is a good one-shot installer. It checks prerequisites, authenticates with Databricks CLI, selects a SQL Warehouse, configures the catalog, builds the frontend, deploys the app, provisions Lakebase, runs the scoring pipeline, syncs data to Lakebase, and prints the app URL.

This install simplicity is strategically valuable. The upgraded platform should keep this spirit.

### 3. Useful adoption scoring model

The current scoring pipeline already demonstrates how to turn system-table telemetry into user achievements. This can remain valuable as:

- ongoing adoption mode
- background telemetry enrichment
- one validator type in GameDay mode
- post-event adoption analytics

### 4. Minimal moving parts

The frontend and backend are simple. This makes the repo approachable and easy to evolve.

## Current limitations

### 1. Mission definitions are hard-coded

Mission definitions are embedded in `app/main.py` and re-implemented in the scoring notebook. That means content changes require code changes. This is the biggest blocker for a GameDay platform.

Required change:

- move quest content to versioned manifests
- load quest packs into tables
- avoid duplicating content in backend and scoring notebooks

### 2. No event model

Current data is user-centric and global. There is no concept of:

- event
- team
- event phase
- event start/end
- participants
- team members
- host role
- scoring freeze
- event-specific leaderboard

Required change:

- introduce `events`, `teams`, `participants`, `team_members`, `event_phases`, and scoped scoring.

### 3. No validation engine

Current mission completion is inferred from system tables. That is useful but insufficient for GameDay because participants need to complete specific tasks and receive feedback.

Required change:

- introduce a first-class validation engine with pluggable validators.

Validator types should include:

- SQL assertion
- Databricks SDK object check
- system-table detector
- notebook validator
- Python code/unit-test validator
- REST/API validator
- manual host validation

### 4. No attempt ledger

A GameDay needs to track every attempt, not just completed missions.

Required tables:

- `task_attempts`
- `validation_runs`
- `validation_results`
- `scoring_events`
- `hints_taken`
- `manual_adjustments`

### 5. No host console

The current admin panel shows aggregate stats and pipeline health. It does not let a host run an event.

Required host features:

- create/import quest pack
- create event
- assign teams
- start/pause/freeze/reset event
- watch validation stream
- issue announcements
- manually award/revoke points
- export event results

### 6. Lakebase sync is deployment-coupled

The deploy script syncs Delta tables to Lakebase after scoring. The scheduled job in `databricks.yml` runs the notebook every four hours, but the current schedule is not clearly coupled to the same Lakebase sync logic as deployment.

For GameDay, leaderboard state must be updated immediately after validation, not only after scheduled telemetry scoring.

Required change:

- use Lakebase as the operational state store for events/attempts/scoring
- write validation results immediately
- sync operational facts to Delta for analytics/audit

### 7. Backend is read-only for users

The current API primarily exposes GET endpoints:

- `/api/profile`
- `/api/missions`
- `/api/leaderboard`
- `/api/notifications`
- `/api/admin/stats`
- `/api/admin/pipeline-status`

GameDay mode needs write workflows:

- create event
- join event
- create team
- start event
- submit attempt
- request hint
- validate task
- manual score adjustment
- send announcement
- reset event

### 8. Frontend is navigation-state-based, not route-based

`App.tsx` uses local page state and four top-level pages. This is fine for the current app but will become fragile once event-specific routes are introduced.

Required change:

- introduce route-like state or React Router
- support URLs like `/events/:eventId`, `/events/:eventId/quests/:questId`, `/host/events/:eventId`

### 9. No content authoring or import workflow

There is no way for field teams to author or load scenarios.

Required change:

- add quest pack schema
- add import endpoint
- add manifest linter
- add sample packs
- add dry-run validator

## Current data model

Current Delta/Lakebase tables:

| Table | Purpose |
|---|---|
| `mission_completions` | mission completions by user |
| `user_points_fact` | points fact table |
| `user_profile_snapshot` | user rollup |
| `leaderboard` | all-time, weekly, monthly leaderboard |
| `badges` | badge unlocks |
| `notifications` | user notifications |

These are good for adoption mode but insufficient for event mode.

## Current frontend structure

| File | Role |
|---|---|
| `frontend/src/App.tsx` | shell, sidebar, topbar, page switching |
| `frontend/src/components/Dashboard.tsx` | user profile, next missions, recent activity, badges |
| `frontend/src/components/Missions.tsx` | mission grid and category filters |
| `frontend/src/components/Leaderboard.tsx` | podium, leaderboard, swag prizes |
| `frontend/src/components/AdminPanel.tsx` | scoring pipeline health and simple analytics |
| `frontend/src/types.ts` | shared TypeScript types |

## Target evolution path

Do not rewrite everything at once. Add GameDay mode as a parallel capability.

Recommended approach:

1. Preserve current adoption mode and endpoints.
2. Add new data model tables.
3. Add quest pack import and active event model.
4. Add validation engine and submission endpoints.
5. Add player event UI.
6. Add host console.
7. Gradually migrate old mission definitions into a built-in adoption quest pack.

## Proposed new repo structure

```text
app/
  main.py
  db.py
  auth.py
  models.py
  repositories/
    events.py
    quest_packs.py
    attempts.py
    leaderboard.py
  services/
    quest_pack_loader.py
    validation_engine.py
    scoring_service.py
    event_lifecycle.py
    resource_bootstrap.py
  validators/
    base.py
    sql_assertion.py
    databricks_sdk.py
    system_table.py
    notebook.py
    python_code.py
    manual.py
  migrations/
    001_gameday_core.sql
    002_validation_engine.sql
    003_audit_and_reporting.sql

frontend/src/
  App.tsx
  routes/
    AdoptionDashboard.tsx
    EventLobby.tsx
    EventDashboard.tsx
    QuestRunner.tsx
    HostConsole.tsx
    QuestPackAdmin.tsx
  components/
    layout/
    quest/
    host/
    leaderboard/
    validation/
    ui/

quest_packs/
  built_in/
    adoption-system-tables.yml
    ai-bi-gameday.yml
    lakehouse-foundations.yml

notebooks/
  scoring_pipeline.py
  sync_lakebase.py
  validation_worker.py
  resource_bootstrap.py
```

## Deep-dive conclusion

The existing repo is a good seed, but its domain model is currently too narrow. The upgrade should not be treated as a UI enhancement. It is a product architecture shift from:

> hard-coded telemetry scoring

To:

> configurable event orchestration + validated technical outcomes + live scoring.
