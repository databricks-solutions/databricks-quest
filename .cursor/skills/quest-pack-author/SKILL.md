---
name: quest-pack-author
description: Use when creating a new Databricks Quest GameDay quest pack or updating an existing one — authoring the YAML manifest, linting it, importing it via the host API, and bumping the immutable version. Triggers on "create a quest pack", "new GameDay quest", "add a quest/task", "update the quest pack", "author a validator", or any quest-pack manifest work in this repo.
---

# Quest Pack Author

Authoritative, in-repo workflow for building and updating **Databricks Quest
GameDay** quest packs. Quest packs are YAML manifests — configuration, not code.
The canonical human guide is [`docs/AUTHORING_QUEST_PACKS.md`](../../../docs/AUTHORING_QUEST_PACKS.md);
this skill is the agent contract. Read the guide if you need field-level depth.

**Reach:** author → lint → import (and version-bump for updates). This skill does
**not** create events or bootstrap resources — that's the host's job.

## Before you start

1. Identify the flow: **Create** (new pack) or **Update** (edit existing).
2. If a live app is available, fetch the **executable validator set** so you never
   claim a check runs when it's actually host-reviewed:
   `GET $APP_URL/api/health` → use `validator_types` + `sdk_checks`. Today only
   `sql_assertion`, `databricks_sdk`/`workspace_api`, and `manual` execute.

## Create flow

1. **Gather intent** (ask the user, don't assume): audience, scenario/narrative,
   duration, number of quests/tasks, and target Databricks capabilities
   (Unity Catalog, SQL warehouse, dashboards, Genie, jobs, pipelines…).
2. **Scaffold** a known-good skeleton:
   `python scripts/new_quest_pack.py --slug <lowercase-dash> --title "<Title>"`
   → writes `quest_packs/built_in/<slug>.yml` (one passing `sql_assertion` task,
   one `manual` task). Edit from there.
3. **Author** quests/tasks/validators/hints/unlock-gating per the rules below.
4. **Lint-iterate** until clean:
   `python scripts/lint_quest_pack.py quest_packs/built_in/<slug>.yml`
   Fix every `error`; review every `warning`.
5. **Import** (host-gated): `POST $APP_URL/api/host/quest-packs/import` with
   `{"manifest_yaml": "<file contents>"}`. Confirm via `GET .../quest-packs`.

## Update flow

1. **Read** the current YAML.
2. **Apply** the edits.
3. **Bump `pack.version`** — versions are **immutable**; re-importing the same
   version with changed content is **rejected** (`ImmutableVersionError`).
   Re-importing identical content is a no-op (`duplicate`). Bump (e.g.
   `0.1.0` → `0.1.1`) so the change lands as a new version (existing events stay
   pinned to their old version).
4. **Re-lint**, then **re-import**.

## Rules (the linter enforces these — get them right the first time)

- **Required:** `schema_version: "1.0"`; `pack` with `slug`/`title`/`version`;
  ≥1 quest; each quest ≥1 task; each task has `slug`/`title`/`objective` **and**
  either ≥1 validator or `manual_validation_required: true`; each validator has
  `id` + `type` (`mode` defaults to `sync`).
- **Slugs:** `^[a-z0-9]+(?:-[a-z0-9]+)*$`. Unique: quest slugs in the pack, task
  slugs in their quest, validator `id`s in their task.
- **Points:** `task.points >= 0`. **Hints:** `penalty_points <= 0` (a penalty;
  positive warns).
- **Unlock gating:** `unlock_rule.type` is `always` or `quest_completed`;
  `quest_completed` needs a `quest_slug` that references a **real, earlier** quest
  and **never itself**.
- **Template variables** are server-resolved from a fixed allowlist — any other
  `${…}` is rejected by the SQL safety layer:
  `event_id, event_slug, team_id, team_slug, team_prefix, team_catalog,
  team_schema, event_catalog, event_schema, event_start, event_end,
  current_user, team_members`. Scope SQL to
  `FROM ${team_catalog}.${team_schema}.<table>`.
- **`sql_assertion`:** read-only `SELECT`/`WITH` only (no `SHOW`/`DESCRIBE`/
  `EXPLAIN`, no DDL/DML, no stacked statements); operators ∈
  `=, !=, >, >=, <, <=, contains, not_contains, is_true, is_false`.
- **`databricks_sdk` checks** (read-only): `dashboard_exists_for_team`,
  `dashboard_published`, `genie_space_exists`, `table_exists`,
  `job_exists_with_schedule`, `pipeline_update_completed`. Confirm the live set
  from `/api/health` before using one.
- **Mandatory fallback:** every `databricks_sdk` task MUST also carry a `manual`
  validator and set `manual_validation_required: true`, so a pilot is never
  blocked if a check can't run (SDK checks degrade to host review).

## Quality bar

- First quest should open with a warehouse-independent check (`SELECT 1`) so
  teams confirm their warehouse binding before timed quests.
- Write `objective`/`instructions_md`/`success_criteria_md` so a host can judge a
  `manual` task and a player knows exactly what to build.
- Always run `scripts/lint_quest_pack.py` before claiming a pack is ready.
