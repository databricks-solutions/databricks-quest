# PR01 Prompt — Domain Model and Migrations

## Branch

`feature/gameday-pr01-domain-model`

## Goal

Add the GameDay data foundation to Lakebase without changing the existing user-facing app.

## Requirements

1. Add a backend DB module that centralizes Lakebase connection handling.
2. Add an idempotent migration runner.
3. Add migration SQL for GameDay operational tables:
   - `schema_migrations`
   - `quest_packs`
   - `quest_pack_versions`
   - `quests`
   - `quest_tasks`
   - `task_validators`
   - `task_hints`
   - `events`
   - `event_hosts`
   - `teams`
   - `participants`
   - `team_members`
   - `task_attempts`
   - `validation_results`
   - `scoring_events`
   - `hints_taken`
   - `announcements`
   - `manual_adjustments`
   - `event_audit_log`
4. Add repository/service stubs for events, quest packs, attempts, and leaderboard.
5. Update `deploy.sh` to run migrations after Lakebase provisioning.
6. Add `/api/health` fields for migration status while preserving current response compatibility.

## Constraints

- Do not remove existing tables.
- Do not remove existing endpoints.
- Do not rewrite the scoring notebook.
- Migration runner must be safe to run repeatedly.
- If Lakebase is unavailable, app should degrade gracefully like it does today.

## Suggested files

```text
app/db.py
app/migrations/run_migrations.py
app/migrations/001_gameday_core.sql
app/repositories/__init__.py
app/repositories/events.py
app/repositories/quest_packs.py
app/repositories/attempts.py
app/repositories/leaderboard.py
app/services/audit.py
```

## Acceptance criteria

- App still imports and starts.
- Existing adoption APIs remain intact.
- Migration runner can apply migrations once and no-op on second run.
- New tables are created in Lakebase.
- `/api/health` indicates `migrations_applied` or equivalent.
- `deploy.sh` runs migrations after Lakebase setup.

## Verification

Run:

```bash
python -m py_compile app/main.py
cd frontend && npm run build
```

If you cannot run Databricks/Lakebase locally, provide manual Databricks verification steps.
