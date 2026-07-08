# Sample Validator Library

> **Start with [`docs/AUTHORING_QUEST_PACKS.md`](../docs/AUTHORING_QUEST_PACKS.md)** — the single guided authoring walkthrough. This file is the copy-paste validator-pattern appendix.

## SQL assertion

```yaml
type: sql_assertion
mode: sync
statement: |
  SELECT COUNT(*) AS cnt
  FROM ${team_catalog}.${team_schema}.target_table
expect:
  operator: ">="
  value: 1
```

Supported operators:

```text
=, !=, >, >=, <, <=, contains, not_contains, is_true, is_false
```

## Databricks SDK — job exists with schedule

```yaml
type: databricks_sdk
mode: sync
check: job_exists_with_schedule
params:
  name_pattern: "${team_slug}-daily-ingest"
  trigger_type: CRON
```

## Databricks SDK — pipeline completed

```yaml
type: databricks_sdk
mode: async
check: pipeline_update_completed
params:
  name_pattern: "${team_slug}-customer-pipeline"
  created_after: "${event_start}"
```

## System table — query executed

```yaml
type: system_table
mode: async
table: system.query.history
condition: |
  executed_by IN (${team_members})
  AND start_time >= ${event_start}
  AND execution_status = 'FINISHED'
  AND LOWER(statement_text) LIKE '%ai_query%'
expect:
  min_rows: 1
```

## Notebook validator

```yaml
type: notebook
mode: async
notebook_path: /Workspace/Shared/quest_validators/check_model_quality
params:
  team_catalog: "${team_catalog}"
  team_schema: "${team_schema}"
  endpoint_name: "${team_slug}-endpoint"
timeout_seconds: 300
```

Notebook output contract:

```json
{
  "status": "passed",
  "message": "Validation passed",
  "score_delta": 200,
  "evidence": {}
}
```

## Manual validator

```yaml
type: manual
mode: async
rubric_md: |
  Award pass if the team can explain the architecture clearly and justify governance choices.
points: 100
```
