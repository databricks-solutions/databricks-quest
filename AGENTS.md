# AGENTS.md — Databricks Quest

This file is the operating guide for AI coding agents working in this repository. It should help agents load the right context quickly, avoid re-discovering the same architecture, and keep the product aligned to the Databricks Quest GameDay vision.

## Product north star

Databricks Quest is evolving from a platform-adoption scoreboard into a configurable Databricks GameDay-style enablement platform for:

- large-scale hands-on events
- sales and solution-engineering enablement
- hunter account motions
- internal Databricks field scale
- customer workshops and competitive team challenges

The product must support two modes:

1. **Adoption Mode** — the existing system-table-driven platform adoption game.
2. **Event Mode** — configurable GameDay-style quests, teams, validations, scoring, leaderboards, host controls, and event reporting.

Do not break Adoption Mode while adding Event Mode.

---

## Read this first

Read only the files relevant to your task. Do not load every document unless the task is broad architecture or planning work.

### Always read before making code changes

1. `README.md`  
   Current product summary, deployment model, existing missions, architecture, and repo structure.

2. `docs/00_EXECUTIVE_SUMMARY.md`  
   The business-level target state and why the project is being levelled up.

3. `docs/13_PR_ALIGNED_SPRINT_PLAN.md`  
   The intended PR sequence. Use this to keep changes small and aligned.

4. The specific prompt in `prompts/` for the PR or task you are executing.  
   Example: for validation-engine work, read `prompts/PR03_VALIDATION_ENGINE_CORE.md`.

### Read for architecture or domain work

5. `docs/03_CODEBASE_DEEP_DIVE.md`  
   Current-state findings, constraints, and refactor targets.

6. `docs/05_TARGET_ARCHITECTURE.md`  
   Target architecture for Adoption Mode + Event Mode.

7. `docs/06_QUEST_MODEL_AND_VALIDATION_ENGINE.md`  
   Quest pack model, validation types, completion flow, and scoring architecture.

8. `docs/07_DATA_MODEL.md`  
   Delta and Lakebase table design. Use this whenever adding or changing persistence.

9. `docs/08_API_CONTRACT.md`  
   Backend API contract. Use this before adding endpoints or changing response shapes.

10. `docs/15_TEST_STRATEGY_AND_ACCEPTANCE_CRITERIA.md`  
    Required validation before finishing a PR.

### Read for event, field, or GTM behaviour

11. `docs/04_AWS_GAMEDAY_RESEARCH_AND_TRANSLATION.md`  
    Explains how the AWS GameDay format maps into Databricks Quest.

12. `docs/10_EVENT_OPERATIONS_PLAYBOOK.md`  
    Facilitator and event-operator workflows.

13. `docs/11_FIELD_AND_HUNTER_ACCOUNT_MOTIONS.md`  
    How the product should support sales, enablement, and account motions.

### Read when modifying quest packs

14. `samples/QUEST_PACK_SCHEMA.md`  
    Canonical quest pack schema.

15. `samples/SAMPLE_QUEST_PACK_AI_BI.md`  
    Reference quest pack.

16. `samples/SAMPLE_VALIDATOR_LIBRARY.md`  
    Reference validator patterns.

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

- `deploy.sh`  
  One-shot deployment flow. Handles Databricks auth, warehouse selection, frontend build, DAB deploy, Lakebase provisioning, scoring run, and Delta-to-Lakebase sync.

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

- Do not remove the current one-shot `deploy.sh` value proposition.
- Preserve non-interactive deployment flags.
- Keep Databricks CLI, Lakebase, DAB, and scoring pipeline flows documented.
- Event Mode should add setup flows, not make basic deployment harder.

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
- event operator change → `docs/10_EVENT_OPERATIONS_PLAYBOOK.md`
- PR sequencing change → `docs/13_PR_ALIGNED_SPRINT_PLAN.md`
- validation behaviour change → `docs/06_QUEST_MODEL_AND_VALIDATION_ENGINE.md`

---

## How to start a task

1. Identify the PR/sprint number or task area.
2. Read this file.
3. Read the relevant prompt in `prompts/`.
4. Read only the docs listed for that task area.
5. Inspect the code files you will modify.
6. Make the smallest coherent change.
7. Run relevant checks.
8. Summarize:
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

