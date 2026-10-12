# Design: Scoring workspace scope (score selected workspaces only)

**Date:** 2026-10-12
**Status:** Approved in design review; build requested
**Repo:** databricks-solutions/databricks-quest

## Summary

An optional, deploy-time setting: a list of workspace IDs. When it is set, the
scoring notebook only uses system-table rows from those workspaces instead of the
whole account. The leaderboard, profiles, badges, notifications and the Insights
page follow automatically, because the app only serves the tables the scoring
notebook writes. Leaving it unset (`all`) keeps today's account-wide behaviour.

```
python deploy.py ... --workspace-ids 1234567890123456,6543210987654321   # scope
python deploy.py ... --workspace-ids all                                 # whole account
python deploy.py ...                                                     # keep current
```

## Decisions from the design review

1. **Deploy time only.** The scope is set with `deploy.py --workspace-ids`. It
   cannot be changed in the app; the app shows it read-only.
2. **No rebuild when the scope changes.** One-time milestones and badges that were
   already earned stay. Everything the pipeline already recomputes every run
   follows the new scope from the next run: weekly DBU points, the 8 repeatable
   missions, the 7 `RESCORE_ONETIME_MISSIONS`, streaks, product breadth and
   activity dates. No new purge logic.
3. **Get Started training is unaffected.** Those points come from the app's
   `training_completions` feed, not system tables.
4. "Where in system tables" means *which workspaces' rows count*, not which
   system tables are read.

## Facts this design rests on (checked 2026-10-12)

- Only `notebooks/scoring_pipeline.py` reads system tables: 57 `FROM`/`JOIN` reads
  over 9 tables (`billing.usage`, `access.audit`, `access.assistant_events`,
  `query.history`, `lakeflow.jobs`, `lakeflow.job_tasks`,
  `lakeflow.job_run_timeline`, `lakeflow.pipelines`,
  `lakeflow.pipeline_update_timeline`). Nothing in `app/` or the other notebooks
  does.
- `workspace_id` is a `string` in all nine tables (official system-table docs).
- Audit: "Account-level audit logs record `workspace_id` as `0`." For Unity
  Catalog and lineage events, "The originating workspace ID is included in
  `request_params.workspace_id`" (audit log reference).
- Live check on an AWS us-east-1 field test workspace, last 30 days: every Unity Catalog action the
  missions read (`createCatalog`, `createExternalLocation`,
  `createStorageCredential`, `createConnection`, `createShare`, `createRecipient`,
  `createRegisteredModel`, `updatePermissions`) is `ACCOUNT_LEVEL` with
  `workspace_id = '0'`, and carries the origin on 94-100% of events
  (`createShare` 706/706, `createRecipient` 419/447, `updatePermissions` 99.7%).
  Every other service the missions read logs a real `workspace_id`. The docs do
  not list `workspace_id` among the parameters of `createShare`/`createRecipient`,
  so that part is observed, not documented: recheck on an Azure account.
- Regional data: only `billing.usage` is global. `query.history`,
  `assistant_events`, the five `lakeflow` tables and workspace-level audit events
  only hold workspaces in the same cloud region as the workspace that queries
  them. A workspace in another region can only earn billing-based and Unity
  Catalog missions (account-level audit events, which include Unity Catalog, are
  global). Confirmed live: from a us-east-1 workspace, a workspace in another region
  showed 0 workspace-level audit rows but 26,543 account-level Unity Catalog rows
  in 45 days.
- `system.access.workspaces_latest` (Public Preview, global) has `workspace_id`,
  `workspace_name`, `workspace_url`, `status`; no region column.
- The scoring job sets no `run_as`, so it runs as its creator. Nothing grants the
  app service principal rights on the job, but the SP does have
  `USE SCHEMA, SELECT, MODIFY` on the Quest schema. So a row in `app_settings`
  would be app-writable; a job parameter is not. That is why the scope is a job
  parameter.
- `jobs.update` replaces same-key tasks wholesale, so a task parameter that
  `scoring_task_graph()` doesn't produce is dropped on redeploy. `deploy.py` must
  carry the current value forward.

## Data flow

```
deploy.py --workspace-ids
   ├─> scoring job, task run_scoring, base_parameters.workspace_ids  ("all" | "id,id")
   │      └─> scoring_pipeline.py widget -> scoped system-table sources
   │             └─> same Quest tables as today -> app reads (unchanged)
   └─> app.yaml env QUEST_SCORING_WORKSPACE_IDS (only when scoped)
          └─> /api/admin/pipeline-status.scoring_workspace_ids -> Insights card
```

## Shared parsing rule (deploy.py, notebook)

Input is a string. Split on commas and whitespace, drop empty tokens.

- One token equal to `all` (any case) means the whole account.
- Otherwise every token must match `^[0-9]{1,19}$` and must not be `0`
  (`0` is the audit marker for account-level events, never a real workspace).
  Duplicates are removed, first occurrence wins, order kept.
- `all` mixed with IDs, or no tokens at all, is an error.

Canonical form: `all`, or the IDs joined with `,`. `tests/test_scoring_scope.py`
keeps the notebook's copy of this rule in lockstep with `deploy.py`'s.

## Component 1: deploy.py

- `Config.workspace_ids: Optional[str] = None` (`None` = flag not given).
- `--workspace-ids` (help: "Only score system-table activity from these workspace
  IDs (comma-separated), or 'all' for the whole account. Omit to keep the current
  setting.").
- `normalize_workspace_ids(raw) -> str` implements the shared rule and raises
  `ValueError` with a message naming the bad token.
- `current_scoring_workspace_ids(w, app_name) -> Optional[str]`: finds the job by
  name, `w.jobs.get`, picks the task with `task_key == "run_scoring"`, returns
  `notebook_task.base_parameters.get("workspace_ids")`. Every level is Optional in
  the SDK, so `None` at any level returns `None`.
- `resolve_scoring_scope(w, app_name, flag)`, called in `run()` before anything is
  created: flag given -> normalized flag (warn when it replaces a different current
  value); else the current job value; else `all` (new job, or one made before this
  setting). If the flag is omitted and the current value can't be read or isn't
  valid (including empty), the deploy stops and asks for `--workspace-ids`:
  falling back to `all` could quietly widen a scoped deployment.
- `scoring_task_graph(..., workspace_ids: str = "all")` adds `workspace_ids` to the
  `run_scoring` params. The default keeps existing positional callers working.
- `render_app_yaml(..., scoring_workspace_ids: str = "all")` emits
  `QUEST_SCORING_WORKSPACE_IDS` only when it isn't `all` (same optional-env pattern
  as `QUEST_ADMIN_ALLOWLIST`).
- Pre-flight, warn-only, only when scoped: one statement on the deploy warehouse
  that, per ID, returns the name and status from `workspaces_latest`, billing rows
  in the last 45 days, and workspace-level audit rows in the last 7 days. Prints
  one line per ID and flags: *not found in this account*, *no billing activity in
  45 days*, *billing but no audit rows here: probably another cloud region, only
  billing and Unity Catalog missions can score*. Any error or timeout prints "couldn't check
  (needs SELECT on system.access and system.billing)" and the deploy continues.
- The deploy summary prints `Scoring scope: whole account` or
  `Scoring scope: N workspace(s) (id, id)`.
- A bundle-managed job is still left untouched (existing behaviour); the warning
  notes the scope wasn't applied to it.

## Component 2: databricks.yml

Keeps the bundle definition mirrored: a `workspace_ids` variable (default empty)
passed to `run_scoring`, and `QUEST_SCORING_WORKSPACE_IDS` in the app env. An
empty value is "not provided", which the notebook guard below handles.

## Component 3: scoring notebook

- Widget `workspace_ids`, default `""` ("not provided").
- Parse with the shared rule. Malformed -> the run fails with the bad token named.
- **Widening guard.** Each run records the scope it used in `app_settings`
  (key `scoring_scope_last_run`, value `""` or the ID list; written only when it
  changes). If a run arrives with no `workspace_ids` and the last run was scoped,
  it fails: "This deployment scores workspaces X. Pass workspace_ids=X, or
  workspace_ids=all for the whole account." `deploy.py` always passes an explicit
  value, so this only catches hand runs (SETUP.md), jobs from an older `deploy.py`,
  and the bundle path. If the record can't be read the run fails rather than
  guessing. The record is not configuration: changing it doesn't change scoring.
- **Scoped sources.** `system_sources(workspace_ids)` maps each table in
  `_SYSTEM_TABLES` to the bare table name when there is no scope, so today's SQL
  runs byte-for-byte, otherwise to `(SELECT * FROM <table> WHERE <predicate>)`.
  Nine constants (`SYS_BILLING_USAGE`,
  `SYS_AUDIT`, `SYS_ASSISTANT_EVENTS`, `SYS_QUERY_HISTORY`, `SYS_JOBS`,
  `SYS_JOB_TASKS`, `SYS_JOB_RUN_TIMELINE`, `SYS_PIPELINES`,
  `SYS_PIPELINE_UPDATE_TIMELINE`) replace all 57 reads. Aliases, time windows,
  human-only filters and joins are unchanged.
- Predicates: `workspace_id IN ('a','b')` for eight tables. Audit:
  `(workspace_id IN ('a','b') OR (workspace_id = '0' AND request_params['workspace_id'] IN ('a','b')))`.
- When scoped, print the scope and, best effort, the names from
  `workspaces_latest` (skipped quietly if that Preview table isn't available).

## Component 4: app

- `app/main.py`: parse `QUEST_SCORING_WORKSPACE_IDS` once (split, strip, keep
  digit tokens). `/api/admin/pipeline-status` (already open to every signed-in
  user) adds `scoring_workspace_ids: string[]`, empty meaning the whole account,
  in both its normal and its fallback response. No new endpoint, no write path.
- Frontend: `PipelineStatus` type gains the field; the "Scoring Pipeline" card on
  Insights shows `Scope: whole account` or `Scope: N workspace(s) (ids) · set at
  deploy time`, in the card's existing text styles. Rebuild and commit
  `app/static`.

## Error handling and edge cases

| Case | Behaviour |
|---|---|
| Malformed `--workspace-ids` | `deploy.py` exits before changing anything |
| Malformed job parameter (hand edit) | Scoring run fails, bad token named |
| Scoped deployment, run with no parameter | Scoring run fails with the fix |
| `app_settings` record unreadable | Scoring run fails (fail closed) |
| Unknown or cancelled workspace ID | Pre-flight warns; it contributes nothing |
| Workspace in another region | Pre-flight warns; only billing and Unity Catalog missions can score |
| UC event without an origin workspace | Not counted while scoped (~0.3% of permission changes, ~6% of `createRecipient`) |
| Account-level event in any other service | Not counted while scoped (none seen for mission actions) |
| Deployer can't read system tables | Pre-flight says it couldn't check; deploy continues |
| Redeploy without the flag | Current scope kept |
| Job created by an older deploy.py | Treated as `all` on the next redeploy |
| Bundle-managed job | Left untouched, warning says the scope wasn't applied |

Performance: scoped reads scan the same files as today; the filter only cuts the
rows that flow into the aggregations. The pre-flight is one statement at deploy
time.

## Testing

- `tests/test_deploy_py.py`: parsing rule (valid, whitespace, duplicates, `all`
  in any case, mixed, non-digit, `0`, empty); `scoring_task_graph` default and
  explicit; resolution (flag wins, current value kept, missing -> `all`, `None` at
  each SDK level); `render_app_yaml` env only when scoped; CLI flag; pre-flight
  stays warn-only when the statement raises.
- `tests/test_scoring_scope.py` (new): no `system.<schema>.<table>` in notebook
  code outside the `SYS_*` definitions; the notebook's parse function behaves the
  same as `deploy.py`'s on a shared case table; with no scope every `SYS_*` equals
  the bare table name; with a scope the predicates are as specified.
- App: `/api/admin/pipeline-status` returns the IDs from the env var, and `[]`
  when it is unset, on both response paths.
- `python -m compileall app notebooks`, the full pytest suite, `npm run test`,
  `npm run build`.
- Live E2E on a field test workspace with a separate app (its own app name, schema and
  warehouse, warehouse backend; the workspace's existing Quest app and its resources
  are not touched): deploy scoped to one workspace; check the job
  parameter, app env and pre-flight output; scoring run succeeds; every scored
  user outside Get Started training has activity in that workspace in the scoped
  sources; Insights shows the scope; redeploy without the flag keeps it; a run
  with an empty parameter fails with the guard message; read-only before/after
  snapshot shows the workspace's existing Quest app unchanged.

## Docs to update

README and `docs/WINDOWS_DEPLOY.md` flag tables; SETUP.md (scope section, the
regional and Unity Catalog notes, `workspace_ids` in the three manual run
commands); `docs/DEPLOY_WITH_GENIE_CODE.md` if it lists flags;
`docs/07_DATA_MODEL.md` (`app_settings` key `scoring_scope_last_run`);
`docs/08_API_CONTRACT.md` (`scoring_workspace_ids`); `docs/17_TROUBLESHOOTING.md`
(guard failure, empty board after scoping, other-region IDs); AGENTS.md (new
missions read system tables through the `SYS_*` sources).

## Out of scope (YAGNI)

- Changing the scope in the app, or the app starting scoring runs.
- Purging or rebuilding awards when the scope changes.
- Per-workspace leaderboards.
- A leaderboard hint about the scope (not requested).
- The existing snake_case `knowledge_assistant` / `supervisor_agent` audit service
  names the notebook doesn't match: a separate, pre-existing bug.
