# AGENTS.md — Databricks Quest

This file is the operating guide for AI coding agents working in this repository. It should help agents load the right context quickly, avoid re-discovering the same architecture, and keep the product aligned to the Databricks Quest GameDay vision.

## Product north star

Databricks Quest is a platform-adoption game: a scoring pipeline reads workspace system tables, and a Databricks App shows each user their missions, points, badges, and the weekly leaderboard. It deploys with `deploy.py`. This is **Adoption Mode**, and it is the product.

The repository also contains **Event Mode** (GameDay) code -- configurable quests, teams, validators, host console, and federation. It is **off by default** (`QUEST_EVENT_MODE`), is **not covered by the user-facing deploy guides**, and has **no supported deploy path**. Treat it as dormant: keep it working where it already lives, but do not advertise it, wire it into deployment, or extend it without an explicit product decision to revive it.

Do not break Adoption Mode.

---

## Read this first

Read only the files relevant to your task. Do not load every document unless the task is broad architecture work.

### Always read before making code changes

1. **`README.md`** -- product summary, deployment model, missions, architecture, and repo structure.
2. **`SETUP.md` / `docs/WINDOWS_DEPLOY.md`** -- how Quest is deployed with `deploy.py`. `docs/DEPLOY_WITH_GENIE_CODE.md` covers the Genie Code path.
3. **`docs/15_TEST_STRATEGY_AND_ACCEPTANCE_CRITERIA.md`** -- required validation before finishing a PR.

### Read for architecture or domain work

4. **`docs/03_CODEBASE_DEEP_DIVE.md`** -- current-state findings and constraints.
5. **`docs/05_TARGET_ARCHITECTURE.md`** -- architecture for Adoption Mode and the dormant Event Mode code.
6. **`docs/06_QUEST_MODEL_AND_VALIDATION_ENGINE.md`** -- quest pack model, validation types, completion flow, and scoring architecture.
7. **`docs/07_DATA_MODEL.md`** -- Delta and Lakebase table design; read before changing persistence.
8. **`docs/08_API_CONTRACT.md`** -- backend API contract; read before adding endpoints or changing response shapes.

---

## Current repo map

- `app/main.py`  
  FastAPI backend. Currently includes hard-coded mission definitions and API endpoints for profile, missions, leaderboard, notifications, and admin stats.

- `frontend/src/App.tsx`  
  React app shell, navigation, profile loading, notifications, and page routing.

- `frontend/src/components/Dashboard.tsx`  
  Existing user dashboard.

- `frontend/src/components/Missions.tsx`  
  Existing mission grid.

- `frontend/src/components/Leaderboard.tsx`  
  Existing leaderboard and swag-prize view.

- `frontend/src/components/AdminPanel.tsx`  
  Existing admin analytics panel.

- `frontend/src/types.ts`  
  Frontend TypeScript interfaces.

- `notebooks/scoring_pipeline.py`  
  Existing system-table scoring pipeline. Creates Delta tables, scores hard-coded missions, builds profiles, leaderboards, badges, and notifications.

- `deploy.py`  
  Cross-platform deployment flow (Databricks SDK): auth, warehouse selection, catalog/schema, app deploy, Lakebase provisioning, the 4-hourly scoring job, and grants. See `SETUP.md` / `docs/WINDOWS_DEPLOY.md`.

- `databricks.yml`  
  Databricks Asset Bundle configuration for the app and scheduled scoring job.

- `app/requirements.txt`  
  Python dependencies for the FastAPI app.

---

## Non-negotiable architecture decisions

1. Keep the product Databricks-native:
   - Databricks Apps for hosting
   - FastAPI backend
   - React frontend
   - Delta tables for durable scoring/history
   - Lakebase for low-latency app reads
   - Databricks Asset Bundles for deployment
   - system tables for passive adoption telemetry
   - Databricks SDK/API for active validation where appropriate

2. Keep Adoption Mode working.
   Existing system-table missions must continue to function while Event Mode is introduced.

3. Do not hard-code future quest content into `app/main.py` or a monolithic notebook.
   New quests must come from configurable quest packs.

4. Validation must become a first-class domain.
   Completion should flow through:

   `quest_attempt -> validation_result -> scoring_event -> leaderboard/profile`

5. Validators must be deterministic, auditable, and safe.
   Store evidence summaries and references, not secrets or sensitive payloads.

6. Event resources must be scoped.
   Any bootstrap/reset/destructive action must be restricted to an event, team, user, catalog, schema, or workspace path prefix created for that event.

7. Do not invent or generate the Databricks logo.
   Use an official Databricks SVG asset supplied by the project owner.

8. Keep PRs focused.
   Do not mix unrelated backend, frontend, deploy, and UX rewrites unless the prompt explicitly requires it.

---

## Implementation rules

### Backend

- Prefer small, typed modules over growing `app/main.py`.
- Use clear service boundaries:
  - event service
  - quest pack service
  - validation service
  - scoring service
  - leaderboard service
  - admin/reporting service
- Use Pydantic models for request and response contracts.
- Keep APIs stable and documented in `docs/08_API_CONTRACT.md`.

### Data model

When adding or changing persistent data, update all relevant locations:

1. Delta DDL / migrations
2. Lakebase DDL
3. Delta-to-Lakebase sync logic
4. FastAPI query logic
5. frontend TypeScript types
6. relevant docs
7. tests or smoke-test notes

### Frontend

- Use React + TypeScript.
- Preserve the Databricks Quest brand system if installed.
- Keep components composable and event-aware.
- Avoid static fake data in production paths; mock data is acceptable only behind clear fallback/demo boundaries.
- Add loading, error, and empty states for all new views.

### Validation engine

Validators should support these patterns:

- `system_table` — passive telemetry validation
- `sql_assertion` — deterministic SQL checks against user/team/event resources
- `notebook_result` — user runs notebook and writes expected output/evidence
- `workspace_api` — checks Databricks workspace objects through SDK/API
- `code_assertion` — validates submitted code, SQL, config, or generated artefacts
- `manual_host_review` — facilitator approval with evidence and reason

Validation results must include:

- event id
- quest id
- mission id
- user id and/or team id
- validator type
- pass/fail status
- evidence summary
- points awarded or score impact
- timestamp
- error message where applicable

### Deploy and operations

- `deploy.py` is the one deployment path. Preserve its non-interactive flags (`--non-interactive`, `--catalog`, `--profile`, `--data-backend`).
- Keep Databricks CLI, Lakebase, and scoring-pipeline flows documented in `SETUP.md` / `docs/WINDOWS_DEPLOY.md`.
- Keep deployment simple; do not reintroduce an Event Mode deploy path without a product decision.

---

## Testing expectations

Before finishing a coding task, run the most relevant checks that are available in the repo:

```bash
cd frontend && npm install && npm run build
python -m compileall app notebooks
```

If tests do not exist yet, add smoke-test notes to the PR summary and identify the missing test coverage.

For data-model work, include at least one idempotency check:

- can the migration run twice?
- can scoring run twice without duplicate points?
- can validation retry without double-awarding?

For validator work, include at least one pass case and one fail case.

---

## Documentation expectations

Update docs in the same PR when changing behaviour.

Common doc updates:

- API change → `docs/08_API_CONTRACT.md`
- table/model change → `docs/07_DATA_MODEL.md`
- architecture change → `docs/05_TARGET_ARCHITECTURE.md`
- deploy change → `SETUP.md` and `docs/WINDOWS_DEPLOY.md`
- validation behaviour change → `docs/06_QUEST_MODEL_AND_VALIDATION_ENGINE.md`

---

## How to start a task

1. Identify the task area.
2. Read this file.
3. Read only the docs listed for that task area.
4. Inspect the code files you will modify.
5. Make the smallest coherent change.
6. Run relevant checks.
7. Summarize:
   - files changed
   - behaviour added/changed
   - tests/checks run
   - known gaps
   - recommended next PR

---

## What not to do

- Do not rewrite the whole repo in one PR.
- Do not delete existing adoption scoring unless explicitly replacing it with a compatible path.
- Do not add new mission types without a validation and scoring path.
- Do not store secrets, tokens, raw credentials, or sensitive evidence in validation results.
- Do not make reset scripts that can delete non-event user assets.
- Do not make UI-only changes that assume backend data will magically exist.
- Do not create Databricks brand marks with image generation.

