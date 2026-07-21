# 18 — Release Checklist

Run this before tagging a release or deploying to a field/customer event. It
covers both Adoption Mode and Event Mode (GameDay). Check every box.

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

- [ ] Deploy with defaults (no `--event-mode`). GameDay routes return 404; Event
      UI hidden; GameDay migrations skipped.
- [ ] `/api/profile`, `/api/missions`, `/api/leaderboard`, `/api/admin/*` respond.
- [ ] Scoring pipeline runs and Delta→Lakebase sync populates the leaderboard.

## 4. Event Mode (GameDay)

- [ ] Deploy with `--event-mode` (and `--admins "<you@corp.com>"`).
- [ ] `/api/health` shows `federation.role`, `validator_types`, and all subsystem
      `checks` healthy (`lakebase`, `migrations`, `validators`, `scoring`,
      `sql_warehouse`).
- [ ] Import a sample pack (`samples/packs/ai_bi_gameday.yml`) — lints with zero
      errors/warnings and imports as an immutable version.
- [ ] Create an event + teams; bootstrap team resources (needs
      `QUEST_SQL_WAREHOUSE_ID`); dry-run plan shows no out-of-namespace targets.
- [ ] Run the full player loop (join → play → submit → score) and confirm the
      live leaderboard updates and hint reveals charge once.
- [ ] Host console: lifecycle transitions, announcements, manual adjustment, and
      the attempts inspector all work.
- [ ] Export the post-event report as JSON, CSV, and Markdown.

## 5. Multi-workspace federation (only if shipping federated)

- [ ] Master deploy (`--role master`) provisions the shared Lakebase + event-
      writer role.
- [ ] Child deploy (`--role child --master-lakebase-host … --master-lakebase-token …`)
      connects; `/api/federation/status` reports `role: child` and `db_connected`.
- [ ] Roster import maps lab users → teams; unmapped scores re-attribute on
      re-import.
- [ ] Global leaderboard spans workspaces; child sees its own team's rank.

## 6. Security & governance

- [ ] Admin/host endpoints enforce the allowlist (non-admin → 403).
- [ ] SQL safety: destructive SQL and template injection are refused
      (`tests/test_security_observability.py`).
- [ ] Validation evidence stores summaries/references only — **no secrets or raw
      sensitive payloads**.
- [ ] Reset/bootstrap cannot touch resources outside the event namespace.
- [ ] Review `docs/12_SECURITY_GOVERNANCE_COST.md` permission model is current.

## 7. Docs

- [ ] `docs/STATUS.md` reflects the shipped PRs.
- [ ] `README.md` (dual-mode) and `README_GAMEDAY.md` intros list the right PRs.
- [ ] `docs/08_API_CONTRACT.md` matches the deployed endpoints.
- [ ] Known limitations captured (see below).

## 8. Manual E2E

- [ ] Walk through `docs/19_MANUAL_E2E_TEST.md` end-to-end on a fresh deploy.

---

## Known limitations (document per release)

- `databricks_sdk` validators are lint-valid but not auto-executed — pair with a
  `manual` validator for completable tasks.
- Metastore-grant live status is admin-gated (no live polling endpoint).
- Resource bootstrap/reset and `sql_assertion` require a SQL warehouse.
- Federation requires a shared Lakebase reachable from child workspaces.
