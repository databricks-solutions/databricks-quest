# PR13 Prompt — Federation Foundation (shared-Lakebase seam)

## Branch

`feature/gameday-pr13-federation-foundation`

## Goal

Add the single-codebase seam for multi-workspace mode (standalone | master |
child) and the shared-Lakebase schema, without changing standalone behaviour.
See `adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md`.

## Requirements

1. Add `app/config.py` reading `QUEST_ROLE` (default `standalone`) and the
   federation env (`QUEST_WORKSPACE_ID`, `QUEST_EVENT_SLUG`, master Lakebase
   coordinates) once at import time, with `is_standalone/is_master/is_child`
   helpers and a `summary()` for `/api/health`.
2. Add an explicit writer-credential branch in `app/db.py`
   (`LAKEBASE_USER`/`LAKEBASE_PASSWORD` or a writer token) used when
   `role=child`; standalone/master keep workspace-identity OAuth.
3. Add `app/migrations/002_federation.sql` (applied once to the shared master
   Lakebase):
   - nullable `workspace_id` on `scoring_events`, `task_attempts`,
     `validation_results`, `hints_taken`, `participants`;
   - `event_workspaces` and `participant_identity_map` tables;
   - redefine `team_scores` to resolve a team via
     `COALESCE(scoring_events.team_id, participant_identity_map.team_id)`;
   - keep `event_leaderboard` shape; add `unmapped_identities` view.
4. Extend `deploy.sh` with `--role`, `--master-lakebase-host/-token/-user`,
   `--event`, `--workspace-id`; child skips local Lakebase provisioning and
   migrations and points at the master; standalone path unchanged.
5. Report the resolved role in `/api/health` (`federation` block).

## Constraints

- One codebase/build for all roles — only runtime parameters differ.
- Every migration statement is idempotent and backward compatible (nullable
  columns, `CREATE ... IF NOT EXISTS` / `CREATE OR REPLACE`).
- Do not change adoption-mode or standalone behaviour.

## Suggested files

```text
app/config.py
app/db.py
app/migrations/002_federation.sql
deploy.sh
adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md
docs/05_TARGET_ARCHITECTURE.md
docs/07_DATA_MODEL.md
```

## Acceptance criteria

- Standalone deploy and adoption mode behave exactly as before.
- Migration 002 runs twice cleanly; `event_leaderboard` keeps the standalone
  `team_id` path.
- `/api/health` reports the role.

## Verification

- `python -m compileall app`
- Run the migration runner twice against a scratch Lakebase; confirm idempotency.
