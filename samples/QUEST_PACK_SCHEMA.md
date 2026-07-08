# Quest Pack Schema

> **Start with [`docs/AUTHORING_QUEST_PACKS.md`](../docs/AUTHORING_QUEST_PACKS.md)** — the single guided scaffold→author→lint→import→version walkthrough. This file is the field-by-field schema appendix.

This is the proposed YAML structure for configurable Databricks Quest GameDay content.

```yaml
schema_version: "1.0"
pack:
  slug: ai-bi-gameday
  title: AI/BI Intelligence Challenge
  version: "0.1.0"
  description: Build a governed data intelligence experience on Databricks.
  audience:
    - data engineers
    - analysts
    - solution architects
  duration_minutes: 120
  difficulty: intermediate
  owner: databricks-field

scenario:
  title: The Executive Intelligence Brief
  narrative_md: |
    Your team has been dropped into Orbit Travel, a fast-growing travel company.
    Leadership needs trusted, governed, AI-ready insights before tomorrow's board meeting.
    The data is messy, access is inconsistent, and the business is moving fast.

learning_objectives:
  - Build governed Delta tables
  - Create useful analytical outputs
  - Validate quality and lineage
  - Create an AI/BI experience

capabilities_required:
  - unity_catalog
  - sql_warehouse
  - dashboards
  - genie_optional

resources:
  team_namespace:
    catalog_template: "${event_catalog}"
    schema_template: "team_${team_slug}"
  seed_data:
    - name: bookings_raw
      type: table
      source: volume:/quest_content/ai_bi/bookings.csv
      target: "${team_catalog}.${team_schema}.bookings_raw"

scoring_defaults:
  max_attempts_without_penalty: 3
  attempt_penalty_after_max: -5
  hints_enabled: true

quests:
  - slug: foundation
    title: Govern the Data Foundation
    category: governance
    difficulty: beginner
    narrative_md: |
      The business does not trust the raw booking data. Create a governed foundation.
    unlock_rule:
      type: always
    tasks:
      - slug: create-bronze-table
        title: Create the raw bookings table
        objective: Create a managed Delta table for raw booking data.
        instructions_md: |
          Use your team schema. The table should be named `bookings_bronze`.
        success_criteria_md: |
          The table exists and contains at least 1,000 rows.
        points: 100
        validators:
          - id: table-exists
            type: sql_assertion
            mode: sync
            statement: |
              SELECT COUNT(*) AS cnt
              FROM ${team_catalog}.${team_schema}.bookings_bronze
            expect:
              operator: ">="
              value: 1000
            timeout_seconds: 15
        hints:
          - title: Table location
            penalty_points: -10
            body_md: Use your assigned team schema and create a managed Delta table.

      - slug: create-quality-view
        title: Create a clean bookings view
        objective: Create a clean view removing invalid records.
        points: 150
        validators:
          - id: clean-view-valid
            type: sql_assertion
            mode: sync
            statement: |
              SELECT COUNT(*) AS invalid_rows
              FROM ${team_catalog}.${team_schema}.bookings_silver
              WHERE booking_id IS NULL OR customer_id IS NULL
            expect:
              operator: "="
              value: 0
```

## Required top-level fields

- `schema_version`
- `pack.slug`
- `pack.title`
- `pack.version`
- `quests[]`
- `quests[].tasks[]`
- `quests[].tasks[].validators[]` unless manual-only

## Validator contract

Every validator must declare:

- `id`
- `type`
- `mode`
- type-specific config
- expected result or custom result contract

## Template variables

Supported variables:

```text
${event_id}
${event_slug}
${team_id}
${team_slug}
${team_catalog}
${team_schema}
${event_catalog}
${event_schema}
${event_start}
${event_end}
${current_user}
${team_members}
```

Template variables must be server-resolved and sanitized.
