# Sample Quest Pack — AI/BI Intelligence Challenge

> **Start with [`docs/AUTHORING_QUEST_PACKS.md`](../docs/AUTHORING_QUEST_PACKS.md)** — the single guided authoring walkthrough. This file is a complete worked-pack appendix.

Below is a sample starter pack. It is intentionally small enough for MVP testing.

```yaml
schema_version: "1.0"
pack:
  slug: ai-bi-intelligence-challenge
  title: AI/BI Intelligence Challenge
  version: "0.1.0"
  description: A two-hour GameDay where teams create governed, trusted business intelligence on Databricks.
  audience: [analysts, data_engineers, solution_architects]
  duration_minutes: 120
  difficulty: intermediate

scenario:
  title: Orbit Travel Board Briefing
  narrative_md: |
    Orbit Travel's leadership team needs a trusted view of bookings, margin, and customer segments.
    Data is scattered and quality is questionable. Your team must build a governed foundation and produce an executive-ready intelligence layer before the board meeting.

resources:
  team_namespace:
    catalog_template: "${event_catalog}"
    schema_template: "team_${team_slug}"
  seed_data:
    - name: bookings_raw
      type: csv
      target: "${team_catalog}.${team_schema}.bookings_raw"

quests:
  - slug: q1-foundation
    title: Build the Governed Foundation
    category: governance
    difficulty: beginner
    tasks:
      - slug: create-bronze-table
        title: Create bookings bronze table
        objective: Create a managed Delta table named `bookings_bronze` in your team schema.
        points: 100
        validators:
          - id: bronze-row-count
            type: sql_assertion
            mode: sync
            statement: |
              SELECT COUNT(*) AS cnt
              FROM ${team_catalog}.${team_schema}.bookings_bronze
            expect:
              operator: ">="
              value: 1000
        hints:
          - title: Start with raw data
            penalty_points: -10
            body_md: Look for `bookings_raw` in your team schema.

      - slug: silver-quality
        title: Remove invalid bookings
        objective: Create `bookings_silver` with invalid booking/customer records removed.
        points: 150
        validators:
          - id: no-null-keys
            type: sql_assertion
            mode: sync
            statement: |
              SELECT COUNT(*) AS invalid_rows
              FROM ${team_catalog}.${team_schema}.bookings_silver
              WHERE booking_id IS NULL OR customer_id IS NULL
            expect:
              operator: "="
              value: 0

  - slug: q2-business-output
    title: Create the Executive Metric Layer
    category: analytics
    difficulty: intermediate
    unlock_rule:
      type: quest_completed
      quest_slug: q1-foundation
    tasks:
      - slug: margin-summary
        title: Build a margin summary table
        objective: Create `margin_summary_gold` with margin by region and product family.
        points: 200
        validators:
          - id: margin-summary-exists
            type: sql_assertion
            mode: sync
            statement: |
              SELECT COUNT(*) AS cnt
              FROM ${team_catalog}.${team_schema}.margin_summary_gold
            expect:
              operator: ">="
              value: 10
          - id: margin-summary-columns
            type: sql_assertion
            mode: sync
            statement: |
              SELECT COUNT(*) AS missing_cols
              FROM (
                SELECT 'region' AS col UNION ALL
                SELECT 'product_family' UNION ALL
                SELECT 'total_margin'
              ) expected
              WHERE expected.col NOT IN (
                SELECT column_name
                FROM system.information_schema.columns
                WHERE table_catalog = '${team_catalog}'
                  AND table_schema = '${team_schema}'
                  AND table_name = 'margin_summary_gold'
              )
            expect:
              operator: "="
              value: 0

  - slug: q3-ai-bi
    title: Deliver the Intelligence Experience
    category: ai_bi
    difficulty: intermediate
    tasks:
      - slug: dashboard-created
        title: Create an executive dashboard
        objective: Create a Databricks dashboard over your gold table.
        points: 200
        validators:
          - id: dashboard-created
            type: databricks_sdk
            mode: sync
            check: dashboard_exists_for_team
            params:
              name_contains: "${team_slug}"
              created_after: "${event_start}"
        hints:
          - title: Dashboard hint
            penalty_points: -20
            body_md: Build the dashboard from the Databricks SQL editor or dashboard experience and include your team slug in the name.
```
