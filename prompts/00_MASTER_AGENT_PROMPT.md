# Master Agent Prompt — Databricks Quest GameDay Platform

You are working in the repository `deepbasu123/databricks-quest`.

Your mission is to evolve the repo from a system-table adoption gamification app into a configurable Databricks GameDay platform while preserving the existing adoption mode.

## Before writing code

Read these files:

- `README.md`
- `SETUP.md`
- `deploy.sh`
- `databricks.yml`
- `app/main.py`
- `app/app.yaml`
- `app/requirements.txt`
- `notebooks/scoring_pipeline.py`
- `frontend/src/App.tsx`
- `frontend/src/types.ts`
- `frontend/src/components/Dashboard.tsx`
- `frontend/src/components/Missions.tsx`
- `frontend/src/components/Leaderboard.tsx`
- `frontend/src/components/AdminPanel.tsx`
- `frontend/package.json`
- `frontend/tailwind.config.js`
- `frontend/src/index.css`

Also read the planning docs in `docs/gameday-levelup/` if they have been added to the repo.

## Product principles

1. Preserve existing adoption mode.
2. Add GameDay event mode incrementally.
3. Quest content must become configurable by manifest.
4. Validation must be first-class.
5. Lakebase should be used for operational event state.
6. Delta should remain the audit/analytics layer.
7. MVP must run inside a single Databricks workspace.
8. Do not require account-level provisioning for MVP.
9. Avoid broad rewrites.
10. Keep deployment simple.

## Technical guardrails

- Keep `./deploy.sh` as the one-shot deployment entrypoint.
- Keep the frontend build path to `app/static`.
- Do not remove existing endpoints.
- Do not delete current scoring notebook.
- Add idempotent migrations for new tables.
- Centralize Lakebase connection logic.
- All mutation endpoints must be auditable.
- Host APIs must enforce roles.
- Validators must be safe and allowlisted.
- SQL validator must block destructive SQL by default.

## Code style

- Prefer small service/repository modules over a single huge `main.py`.
- Use Pydantic models for request/response types if adding backend models.
- Keep frontend TypeScript types explicit.
- Use lightweight dependencies only.
- Add docstrings where behavior is non-obvious.

## Verification expectations

For every PR:

- Run `cd frontend && npm run build` if frontend changed.
- Run any available backend syntax/test checks.
- Provide manual test steps.
- Confirm existing adoption pages still load conceptually.
- Summarize files changed and risk.

## Delivery process

Work one PR prompt at a time. Do not implement future PRs early unless a small foundation is necessary and explicitly documented.
