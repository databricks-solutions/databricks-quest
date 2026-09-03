# 18 — Release Checklist

Run this before tagging a release or deploying to a field/customer event. It
covers Adoption Mode. Check every box.

## 1. Code & build

- [ ] `git status` clean (no stray local config like `app/app.yaml` env edits).
- [ ] Backend compiles: `python -m compileall app notebooks`.
- [ ] Backend tests pass: `pytest tests/` (expect all green).
- [ ] Frontend builds: `cd frontend && npm install && npm run build` (tsc + vite,
      no errors).
- [ ] No secrets, tokens, or credentials committed (the Databricks pre-commit
      hook scans staged changes — do not bypass it).

## 2. Migrations & data model

- [ ] Migrations are idempotent — running `app/migrations/run_migrations.py`
      twice is a no-op the second time.
- [ ] Any data-model change is reflected in **all** of: Delta DDL / migration,
      Lakebase DDL, sync logic, FastAPI queries, frontend `types.ts`, and
      `docs/07_DATA_MODEL.md`.
- [ ] Scoring is idempotent — re-running scoring / re-submitting an attempt does
      not double-award (idempotency key includes `workspace_id` for federation).

## 3. Adoption Mode (must keep working)

- [ ] Deploy with defaults. With `QUEST_EVENT_MODE` unset, GameDay routes return
      404, the Event UI is hidden, and GameDay migrations are skipped.
- [ ] `/api/profile`, `/api/missions`, `/api/leaderboard`, `/api/admin/*` respond.
- [ ] Scoring pipeline runs and Delta→Lakebase sync populates the leaderboard.

## 4. Security & governance

- [ ] Admin/host endpoints enforce the allowlist (non-admin → 403).
- [ ] SQL safety: destructive SQL and template injection are refused
      (`tests/test_security_observability.py`).
- [ ] Validation evidence stores summaries/references only — **no secrets or raw
      sensitive payloads**.
- [ ] Reset/bootstrap cannot touch resources outside the event namespace.
- [ ] Review `docs/12_SECURITY_GOVERNANCE_COST.md` permission model is current.

## 5. Docs

- [ ] `README.md` intro is current.
- [ ] `docs/08_API_CONTRACT.md` matches the deployed endpoints.
- [ ] Known limitations captured (see below).

## 6. Manual E2E

- [ ] Walk through `docs/19_MANUAL_E2E_TEST.md` end-to-end on a fresh deploy.

---

## Known limitations (document per release)

- `databricks_sdk` validators are lint-valid but not auto-executed — pair with a
  `manual` validator for completable tasks.
- Metastore-grant live status is admin-gated (no live polling endpoint).
- Resource bootstrap/reset and `sql_assertion` require a SQL warehouse.
