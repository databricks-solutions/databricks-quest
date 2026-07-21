# Databricks notebook source
# MAGIC %md
# MAGIC # Databricks Quest - Scoring Pipeline
# MAGIC
# MAGIC Reads system tables, detects mission completions, computes user profiles,
# MAGIC leaderboards, badges, and notifications. Idempotent via MERGE.
# MAGIC
# MAGIC ## Human activity only (no automation double-counting)
# MAGIC Quest is a *human* adoption game. `system.billing.usage.identity_metadata.run_as`
# MAGIC logs whoever a workload runs *as* — for a scheduled job or continuous pipeline
# MAGIC that is the creator, not a person at the keyboard. So a cron job or DLT pipeline
# MAGIC keeps banking points under its owner's name even while they're on leave, which
# MAGIC inflates the leaderboard (verified on real data: ~79% of consumption DBUs come
# MAGIC from job/pipeline rows).
# MAGIC
# MAGIC The rule this pipeline enforces:
# MAGIC   * **Creating** an automated workload is rewarded **once** (Job Creator,
# MAGIC     Pipeline Builder, Scheduler, Multi-Task Orchestrator, Auto Loader Pioneer —
# MAGIC     each keyed on the first `creator`/`created_by` event, so it fires a single
# MAGIC     time regardless of how often the workload later runs).
# MAGIC   * **Ongoing consumption / activity** points count **interactive human work
# MAGIC     only**. Billing rows carrying a `job_id` or `dlt_pipeline_id` are automated
# MAGIC     and excluded (`INTERACTIVE_USAGE` filter below). Run-based missions count
# MAGIC     only human-triggered runs (`trigger_type` = ONETIME for jobs / USER_ACTION
# MAGIC     for pipelines). Query-count missions exclude job/pipeline-sourced queries.
# MAGIC
# MAGIC Column names verified against the official system-tables reference and live data.

# COMMAND ----------

# Reusable predicate: a system.billing.usage row is INTERACTIVE (human at the
# keyboard) when it is not attributed to a scheduled job run or a pipeline update.
# usage_metadata.job_id / dlt_pipeline_id are populated only for automated compute.
INTERACTIVE_USAGE = (
    "usage_metadata.job_id IS NULL AND usage_metadata.dlt_pipeline_id IS NULL"
)

# Products that represent a HUMAN actively driving compute, used for the weekly
# DBU consumption points. This is an ALLOW-list (not an exclude-list) on purpose:
# always-on / machine products must not leak into a human adoption leaderboard,
# and an allow-list means a NEW machine product can't silently start counting.
#   ALL_PURPOSE  - interactive clusters (notebooks/REPL)
#   INTERACTIVE  - serverless interactive notebooks/SQL
#   SQL          - SQL warehouse queries (editor/dashboards)
#   AI_FUNCTIONS - a person invoking ai_query() etc.
#   GENIE        - a person asking questions in Genie
# Deliberately EXCLUDED (machine / always-on, even when job_id is null):
#   MODEL_SERVING (inference endpoints answer requests 24/7 — this was letting an
#   endpoint owner top the leaderboard on ~369k DBUs of automated inference),
#   DATA_QUALITY_MONITORING, AGENT_EVALUATION, FEATURE_STORE, VECTOR_SEARCH,
#   LAKEBASE, AI_GATEWAY, etc. Deploying a model / serving are still rewarded via
#   the model_deployer and ml_practitioner missions, just not this raw DBU pool.
INTERACTIVE_PRODUCTS = ('ALL_PURPOSE', 'INTERACTIVE', 'SQL', 'AI_FUNCTIONS', 'GENIE')

# Safety cap: no single user can bank more than this many DBU-consumption points
# in one week, so raw compute volume alone can never dominate the human-adoption
# leaderboard even within the interactive products.
WEEKLY_CONSUMPTION_POINT_CAP = 500

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuration

# COMMAND ----------

dbutils.widgets.text("quest_catalog", "your_catalog", "Quest Catalog Name")
dbutils.widgets.text("quest_schema", "quest", "Quest Schema Name")
dbutils.widgets.text("app_name", "databricks-quest", "Databricks App Name (for auto-granting permissions)")

CATALOG = dbutils.widgets.get("quest_catalog")
SCHEMA = dbutils.widgets.get("quest_schema")
APP_NAME = dbutils.widgets.get("app_name")

# Lookback window over system-table data. 45 days (up from 30) so that one-time
# "creation" missions (job_creator, first_steps, etc.) aren't permanently missed
# when the creating event is a few weeks old at first score — while staying well
# short of a window that materially lengthens the scans over the 100M+ row
# query.history / audit tables. One-time missions are idempotent (WHEN NOT
# MATCHED), so once earned they persist regardless of the window.
LOOKBACK_DAYS = 45

def tbl(name):
    return f"`{CATALOG}`.`{SCHEMA}`.`{name}`"

print(f"Quest output: {CATALOG}.{SCHEMA} (lookback: {LOOKBACK_DAYS} days)")

# Note: If you get ConcurrentAppendException, ensure no other scoring job is
# running simultaneously. The MERGE operations read the full table.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1: Create Catalog, Schema, and Tables
# MAGIC Auto-creates the catalog and schema if they don't exist.

# COMMAND ----------

try:
    spark.sql(f"CREATE CATALOG IF NOT EXISTS `{CATALOG}`")
    print(f"Catalog `{CATALOG}` ready.")
except Exception as e:
    # Some workspaces require a MANAGED LOCATION for new catalogs.
    # If the catalog already exists, USE CATALOG will succeed anyway.
    print(f"Note: CREATE CATALOG failed ({e}). Checking if catalog already exists...")

try:
    spark.sql(f"USE CATALOG `{CATALOG}`")
except Exception as e:
    raise RuntimeError(
        f"Catalog `{CATALOG}` does not exist and could not be auto-created. "
        f"Please create it manually in your workspace (Catalog > + Add > Add a catalog) "
        f"and re-run the pipeline."
    ) from e

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`")

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('mission_completions')} (
  user_id STRING,
  mission_id STRING,
  mission_name STRING,
  points_awarded INT,
  completed_at TIMESTAMP,
  period_start DATE,
  period_end DATE,
  scored_at TIMESTAMP
)
USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('user_points_fact')} (
  user_id STRING,
  event_type STRING,
  mission_id STRING,
  points INT,
  reason STRING,
  event_timestamp TIMESTAMP,
  scored_at TIMESTAMP
)
USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('user_profile_snapshot')} (
  user_id STRING,
  display_name STRING,
  total_points INT,
  level STRING,
  current_streak INT,
  max_streak INT,
  badge_count INT,
  missions_completed INT,
  first_activity_date DATE,
  last_activity_date DATE,
  distinct_products_used INT,
  updated_at TIMESTAMP
)
USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('leaderboard')} (
  user_id STRING,
  display_name STRING,
  total_points INT,
  weekly_points INT,
  monthly_points INT,
  level STRING,
  all_time_rank INT,
  weekly_rank INT,
  monthly_rank INT,
  updated_at TIMESTAMP
)
USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('badges')} (
  user_id STRING,
  badge_id STRING,
  badge_name STRING,
  badge_icon STRING,
  earned_at TIMESTAMP
)
USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('notifications')} (
  user_id STRING,
  notification_type STRING,
  title STRING,
  message STRING,
  mission_id STRING,
  points INT,
  created_at TIMESTAMP
)
USING DELTA
""")

print("All tables created successfully.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2: Score Missions
# MAGIC Each mission is scored via a MERGE to ensure idempotency.

# COMMAND ----------

from datetime import datetime

NOW = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

# Repeatable, period-scoped missions are recomputable aggregates: each run
# re-derives who qualifies within a rolling window. They are scored with
# `WHEN NOT MATCHED` (insert-if-missing), so a completion recorded under older/
# looser logic would stick forever even after the qualifying rule tightens
# (e.g. a user who only "qualified" for Consistent Operator via scheduled runs,
# before the human-triggered filter). Clear them up front so the MERGEs below
# rebuild them under the current filters. One-time achievement missions are NOT
# cleared — those are genuine historical milestones and stay append-only.
REPEATABLE_MISSIONS = (
    "genie_power_user", "data_explorer", "power_analyst", "sql_analyst",
    "ml_practitioner", "consistent_operator", "daily_driver", "cross_product_champion",
)
_rep_in = ", ".join(f"'{m}'" for m in REPEATABLE_MISSIONS)
_rep_n = spark.sql(
    f"SELECT COUNT(*) AS c FROM {tbl('mission_completions')} WHERE mission_id IN ({_rep_in})"
).first()["c"]
if _rep_n:
    spark.sql(f"DELETE FROM {tbl('mission_completions')} WHERE mission_id IN ({_rep_in})")
    # Step 3 mirrors mission_completions into user_points_fact with an
    # insert-if-missing MERGE, so clear the matching fact rows too or the deleted
    # completions' points would survive there and keep counting.
    spark.sql(
        f"DELETE FROM {tbl('user_points_fact')} "
        f"WHERE event_type = 'mission_completion' AND mission_id IN ({_rep_in})"
    )
    print(f"Cleared {_rep_n} repeatable-mission rows (and their fact rows) for recompute under current filters.")

# COMMAND ----------

# One-time missions whose DETECTION CRITERIA changed after they were first scored.
# They are insert-if-missing, so rows awarded under the old (wrong) logic would
# stick even though the rule changed — e.g. uc_publisher wrongly credited the
# notebook runner for the pipeline's own self-grant, and auto_loader_pioneer
# over-awarded via the old billing-join. Clear these once so they rebuild under
# the corrected detection below. Safe & idempotent: legitimately-qualifying users
# are simply re-inserted this run. Remove IDs from this list once all live
# deployments have re-scored past the fix (optional cleanup — harmless to keep).
RESCORE_ONETIME_MISSIONS = (
    "uc_publisher", "liquid_clustering", "auto_loader_pioneer",
    "mlflow_experimenter", "vector_search_pioneer", "stream_starter",
)
_ro_in = ", ".join(f"'{m}'" for m in RESCORE_ONETIME_MISSIONS)
_ro_n = spark.sql(
    f"SELECT COUNT(*) AS c FROM {tbl('mission_completions')} WHERE mission_id IN ({_ro_in})"
).first()["c"]
if _ro_n:
    spark.sql(f"DELETE FROM {tbl('mission_completions')} WHERE mission_id IN ({_ro_in})")
    spark.sql(
        f"DELETE FROM {tbl('user_points_fact')} "
        f"WHERE event_type = 'mission_completion' AND mission_id IN ({_ro_in})"
    )
    print(f"Cleared {_ro_n} changed-one-time-mission rows for recompute under corrected detection.")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: First Steps (25 pts)
# MAGIC First billable Databricks usage per user.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    identity_metadata.run_as AS user_id,
    'first_steps' AS mission_id,
    'First Steps' AS mission_name,
    25 AS points_awarded,
    CAST(MIN(usage_date) AS TIMESTAMP) AS completed_at,
    MIN(usage_date) AS period_start,
    MIN(usage_date) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.billing.usage
  WHERE identity_metadata.run_as IS NOT NULL
    AND identity_metadata.run_as != ''
    AND identity_metadata.run_as NOT LIKE '%service-principal%'
    AND identity_metadata.run_as NOT LIKE '%ServicePrincipal%'
    AND usage_quantity > 0
    -- NOTE: deliberately NOT filtered to interactive-only. First Steps is a
    -- one-time 25-pt "entry" award keyed on MIN(usage_date) (first-ever usage),
    -- so it cannot inflate a leaderboard. Requiring interactive usage would make
    -- it unearnable for engineers whose first activity is a job/pipeline they set
    -- up — they'd get Job Creator but not the beginner mission. Any first usage
    -- legitimately marks a human entering the platform (SP rows are swept later).
    AND usage_date >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY identity_metadata.run_as
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: First Steps")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Job Creator (100 pts)
# MAGIC Create first Lakeflow Job.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    creator_user_name AS user_id,
    'job_creator' AS mission_id,
    'Job Creator' AS mission_name,
    100 AS points_awarded,
    MIN(change_time) AS completed_at,
    CAST(MIN(change_time) AS DATE) AS period_start,
    CAST(MIN(change_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.lakeflow.jobs
  WHERE creator_user_name IS NOT NULL
    AND creator_user_name != ''
    AND delete_time IS NULL
    AND change_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY creator_user_name
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Job Creator")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Pipeline Builder (150 pts)
# MAGIC Create first Lakeflow Spark Declarative Pipeline.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    created_by AS user_id,
    'pipeline_builder' AS mission_id,
    'Pipeline Builder' AS mission_name,
    150 AS points_awarded,
    MIN(change_time) AS completed_at,
    CAST(MIN(change_time) AS DATE) AS period_start,
    CAST(MIN(change_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.lakeflow.pipelines
  WHERE created_by IS NOT NULL
    AND created_by != ''
    AND delete_time IS NULL
    AND pipeline_type = 'ETL_PIPELINE'
    AND change_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY created_by
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Pipeline Builder")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Pipeline Runner (200 pts)
# MAGIC First successful pipeline update.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    run_as_user_name AS user_id,
    'pipeline_runner' AS mission_id,
    'Pipeline Runner' AS mission_name,
    200 AS points_awarded,
    MIN(period_start_time) AS completed_at,
    CAST(MIN(period_start_time) AS DATE) AS period_start,
    CAST(MIN(period_start_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.lakeflow.pipeline_update_timeline
  WHERE result_state = 'COMPLETED'
    AND run_as_user_name IS NOT NULL
    AND run_as_user_name != ''
    -- One-time award (fires once, on the first successful update). Intentionally
    -- not restricted to USER_ACTION: this rewards the milestone "your pipeline
    -- ran successfully" a single time, which is the same first-time-setup credit
    -- we want. It cannot inflate the leaderboard (one_time), and a USER_ACTION
    -- filter would make it unearnable for a schedule-only owner. Recurring runs
    -- are what we don't reward — that's handled by the consumption/streak filters.
    AND period_start_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY run_as_user_name
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Pipeline Runner")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Scheduler (150 pts)
# MAGIC Create a scheduled or CRON-triggered job.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    j.creator_user_name AS user_id,
    'scheduler' AS mission_id,
    'Scheduler' AS mission_name,
    150 AS points_awarded,
    MIN(j.change_time) AS completed_at,
    CAST(MIN(j.change_time) AS DATE) AS period_start,
    CAST(MIN(j.change_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC) AS rn
    FROM system.lakeflow.jobs
    WHERE change_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  ) j
  WHERE j.rn = 1
    AND j.trigger_type IN ('CRON', 'PERIODIC')
    AND j.delete_time IS NULL
    AND j.creator_user_name IS NOT NULL
    AND j.creator_user_name != ''
  GROUP BY j.creator_user_name
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Scheduler")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Auto Loader Pioneer (250 pts)
# MAGIC Actually use Auto Loader — detected by the `cloudFiles` format or the
# MAGIC `read_files` streaming source appearing in the user's executed SQL. The
# MAGIC previous version only checked that *any* DLT pipeline ran (billing join on
# MAGIC dlt_pipeline_id), which credited every pipeline author whether or not they
# MAGIC used Auto Loader. This looks for the real Auto Loader signal instead.
# MAGIC NOTE: Auto Loader used purely inside a Python pipeline notebook won't show
# MAGIC in query.history; this detects the SQL-surfaced usage, which is precise
# MAGIC (no false positives) at the cost of missing some pipeline-only usage.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    executed_by AS user_id,
    'auto_loader_pioneer' AS mission_id,
    'Auto Loader Pioneer' AS mission_name,
    250 AS points_awarded,
    CAST(MIN(start_time) AS TIMESTAMP) AS completed_at,
    CAST(MIN(start_time) AS DATE) AS period_start,
    CAST(MIN(start_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.query.history
  WHERE executed_by IS NOT NULL AND executed_by != ''
    AND (LOWER(statement_text) LIKE '%cloudfiles%' OR LOWER(statement_text) LIKE '%read_files%')
    AND start_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY executed_by
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Auto Loader Pioneer")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Consistent Operator (300 pts, repeatable monthly)
# MAGIC Run pipelines or jobs on 7 distinct days within rolling 30 days.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  WITH daily_runs AS (
    -- Human-triggered job runs only. trigger_type ONETIME = manual "Run now",
    -- API submit, or notebook workflow (a person acted). CRON/PERIODIC/CONTINUOUS/
    -- TABLE/FILE_ARRIVAL are automated schedules and must not sustain the streak.
    -- job_run_timeline has no run_as column, so attribute to the job creator.
    SELECT
      j.creator_user_name AS user_id,
      CAST(r.period_start_time AS DATE) AS run_date
    FROM system.lakeflow.job_run_timeline r
    JOIN (
      SELECT job_id, creator_user_name, workspace_id,
             ROW_NUMBER() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC) AS rn
      FROM system.lakeflow.jobs
    ) j ON r.job_id = j.job_id AND r.workspace_id = j.workspace_id AND j.rn = 1
    WHERE r.result_state IS NOT NULL
      AND r.trigger_type = 'ONETIME'
      AND j.creator_user_name IS NOT NULL AND j.creator_user_name != ''
      AND r.period_start_time >= DATE_SUB(CURRENT_DATE(), 30)
    UNION
    -- Human-triggered pipeline updates only (USER_ACTION = started from the UI).
    SELECT
      run_as_user_name AS user_id,
      CAST(period_start_time AS DATE) AS run_date
    FROM system.lakeflow.pipeline_update_timeline
    WHERE result_state IS NOT NULL
      AND trigger_type = 'USER_ACTION'
      AND run_as_user_name IS NOT NULL AND run_as_user_name != ''
      AND period_start_time >= DATE_SUB(CURRENT_DATE(), 30)
  )
  SELECT
    user_id,
    'consistent_operator' AS mission_id,
    'Consistent Operator' AS mission_name,
    300 AS points_awarded,
    CAST(MAX(run_date) AS TIMESTAMP) AS completed_at,
    DATE_SUB(CURRENT_DATE(), 30) AS period_start,
    CURRENT_DATE() AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM daily_runs
  GROUP BY user_id
  HAVING COUNT(DISTINCT run_date) >= 7
) AS source
ON target.user_id = source.user_id
  AND target.mission_id = source.mission_id
  AND target.period_start = source.period_start
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Consistent Operator")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Data Explorer (150 pts, repeatable weekly)
# MAGIC Execute 50+ SQL queries in a week.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    executed_by AS user_id,
    'data_explorer' AS mission_id,
    'Data Explorer' AS mission_name,
    150 AS points_awarded,
    CAST(MAX(start_time) AS TIMESTAMP) AS completed_at,
    DATE_TRUNC('WEEK', start_time) AS period_start,
    DATE_ADD(DATE_TRUNC('WEEK', start_time), 6) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.query.history
  WHERE executed_by IS NOT NULL
    AND executed_by != ''
    AND statement_type IN ('SELECT', 'INSERT', 'MERGE', 'CREATE', 'ALTER')
    -- Human ad-hoc queries only: exclude queries issued by a job or pipeline.
    -- Check the inner id — the job_info/pipeline_info struct itself is usually
    -- present (non-null) even for interactive queries; only the id is populated
    -- when the query actually originated from a job run or pipeline update.
    AND query_source.job_info.job_id IS NULL
    AND query_source.pipeline_info.pipeline_id IS NULL
    AND execution_status = 'FINISHED'
    AND start_time >= DATE_SUB(CURRENT_DATE(), 30)
  GROUP BY executed_by, DATE_TRUNC('WEEK', start_time)
  -- Tiered with Power Analyst: 50-199 queries = Data Explorer (150 pts);
  -- 200+ = Power Analyst (200 pts) only. A week never pays both.
  HAVING COUNT(*) >= 50 AND COUNT(*) < 200
) AS source
ON target.user_id = source.user_id
  AND target.mission_id = source.mission_id
  AND target.period_start = source.period_start
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Data Explorer")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Genie Creator (200 pts)
# MAGIC Create your first AI/BI Genie space.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'genie_creator' AS mission_id,
    'Genie Creator' AS mission_name,
    200 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE action_name = 'genieCreateSpace'
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL
    AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Genie Creator")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Dashboard Designer (150 pts)
# MAGIC Create your first Databricks Dashboard (Lakeview).

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'dashboard_designer' AS mission_id,
    'Dashboard Designer' AS mission_name,
    150 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE action_name = 'createDashboard'
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL
    AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Dashboard Designer")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Genie Explorer (100 pts)
# MAGIC Ask a question in an AI/BI Genie space (business-user adoption of Genie).

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'genie_explorer' AS mission_id,
    'Genie Explorer' AS mission_name,
    100 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name = 'aibiGenie'
    AND action_name IN ('genieStartConversationMessage', 'genieCreateConversationMessage')
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL
    AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Genie Explorer")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Dashboard Explorer (75 pts)
# MAGIC Open and view a published AI/BI dashboard (business-user consumption).

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'dashboard_viewer' AS mission_id,
    'Dashboard Explorer' AS mission_name,
    75 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name = 'dashboards'
    AND action_name IN ('getPublishedDashboard', 'getPublishedDashboardEmbedded')
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL
    AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Dashboard Explorer")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: AI Assistant (100 pts)
# MAGIC Use the Databricks Assistant (Genie / Genie Code) to write or fix code.
# MAGIC Sourced from system.access.assistant_events (initiated_by is the user email).

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    initiated_by AS user_id,
    'genie_code_user' AS mission_id,
    'AI Assistant' AS mission_name,
    100 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.assistant_events
  WHERE initiated_by IS NOT NULL
    AND initiated_by LIKE '%@%'
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY initiated_by
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: AI Assistant")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Lakebase Builder (250 pts)
# MAGIC Create your first Lakebase (managed Postgres / OLTP) database instance.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'lakebase_builder' AS mission_id,
    'Lakebase Builder' AS mission_name,
    250 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name = 'databaseInstances'
    AND action_name = 'createDatabaseInstance'
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL
    AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Lakebase Builder")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Genie Curator (150 pts)
# MAGIC Add instructions or sample/curated questions to tune a Genie space for a team.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'genie_curator' AS mission_id,
    'Genie Curator' AS mission_name,
    150 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name = 'aibiGenie'
    AND action_name IN ('createInstruction', 'createCuratedQuestion', 'updateSampleQuestions')
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Genie Curator")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Genie Power User (100 pts, repeatable weekly)
# MAGIC Ask 10+ Genie questions in a single week.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'genie_power_user' AS mission_id,
    'Genie Power User' AS mission_name,
    100 AS points_awarded,
    CAST(MAX(event_time) AS TIMESTAMP) AS completed_at,
    DATE_TRUNC('WEEK', event_time) AS period_start,
    DATE_ADD(DATE_TRUNC('WEEK', event_time), 6) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name = 'aibiGenie'
    AND action_name IN ('createConversationMessage', 'genieCreateConversationMessage')
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), 30)
  GROUP BY user_identity.email, DATE_TRUNC('WEEK', event_time)
  HAVING COUNT(*) >= 10
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id AND target.period_start = source.period_start
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Genie Power User")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Dashboard Publisher (150 pts)
# MAGIC Publish an AI/BI dashboard so others can use it.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'dashboard_publisher' AS mission_id,
    'Dashboard Publisher' AS mission_name,
    150 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name = 'dashboards'
    AND action_name = 'publishDashboard'
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Dashboard Publisher")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Dashboard Operator (150 pts)
# MAGIC Schedule a dashboard delivery or subscription.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'dashboard_operator' AS mission_id,
    'Dashboard Operator' AS mission_name,
    150 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name = 'dashboards'
    AND action_name IN ('createSchedule', 'createSubscription', 'setSubscriptions')
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Dashboard Operator")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Query Author (75 pts)
# MAGIC Save a query in the Databricks SQL editor.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'query_author' AS mission_id,
    'Query Author' AS mission_name,
    75 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name = 'databrickssql'
    AND action_name = 'createQuery'
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Query Author")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: App Builder (250 pts)
# MAGIC Create and deploy a Databricks App.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'app_builder' AS mission_id,
    'App Builder' AS mission_name,
    250 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name = 'apps'
    AND action_name IN ('createApp', 'deployApp')
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: App Builder")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Notebook Author (75 pts)
# MAGIC Create your first notebook.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'notebook_author' AS mission_id,
    'Notebook Author' AS mission_name,
    75 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name = 'notebook'
    AND action_name = 'createNotebook'
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Notebook Author")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Lakebase Sync Builder (250 pts)
# MAGIC Sync a Unity Catalog table into Lakebase (synced table).

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'lakebase_sync' AS mission_id,
    'Lakebase Sync Builder' AS mission_name,
    250 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name IN ('databaseInstances', 'postgres')
    AND action_name IN ('createSyncedDatabaseTable', 'createSyncedTable')
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Lakebase Sync Builder")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Lakebase Database Creator (150 pts)
# MAGIC Create a Lakebase database or register a Lakebase catalog in Unity Catalog.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'lakebase_database' AS mission_id,
    'Lakebase Database Creator' AS mission_name,
    150 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name IN ('databaseInstances', 'postgres')
    AND action_name IN ('createDatabaseCatalog', 'createDatabase')
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Lakebase Database Creator")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Lakebase Connector (100 pts)
# MAGIC Connect to Lakebase from an app or client (generate a database credential).

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'lakebase_connector' AS mission_id,
    'Lakebase Connector' AS mission_name,
    100 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE service_name IN ('databaseInstances', 'postgres')
    AND action_name = 'generateDatabaseCredential'
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL AND user_identity.email != ''
    AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Lakebase Connector")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Multi-Task Orchestrator (200 pts)
# MAGIC Create a workflow with 3+ tasks.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  -- Qualify a user if ANY SINGLE job they created has 3+ distinct tasks.
  -- Count tasks PER JOB first (inner GROUP BY workspace_id, job_id), then keep
  -- jobs with >=3 tasks, then reduce to one row per creator. The previous
  -- version grouped by creator only and counted task_keys across ALL their
  -- jobs, so three separate 1-task jobs wrongly qualified (~28% over-award).
  SELECT
    creator_user_name AS user_id,
    'multi_task_orchestrator' AS mission_id,
    'Multi-Task Orchestrator' AS mission_name,
    200 AS points_awarded,
    MIN(first_task_time) AS completed_at,
    CAST(MIN(first_task_time) AS DATE) AS period_start,
    CAST(MIN(first_task_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM (
    SELECT
      j.creator_user_name,
      j.job_id,
      MIN(j.change_time) AS first_task_time,
      COUNT(DISTINCT t.task_key) AS task_count
    FROM (
      SELECT *, ROW_NUMBER() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC) AS rn
      FROM system.lakeflow.jobs
      WHERE change_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
    ) j
    JOIN system.lakeflow.job_tasks t ON j.job_id = t.job_id AND j.workspace_id = t.workspace_id
    WHERE j.rn = 1
      AND j.delete_time IS NULL
      AND j.creator_user_name IS NOT NULL AND j.creator_user_name != ''
    GROUP BY j.creator_user_name, j.job_id
    HAVING COUNT(DISTINCT t.task_key) >= 3
  )
  GROUP BY creator_user_name
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Multi-Task Orchestrator")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Power Analyst (200 pts)
# MAGIC Execute 200+ SQL queries in a week.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    executed_by AS user_id,
    'power_analyst' AS mission_id,
    'Power Analyst' AS mission_name,
    200 AS points_awarded,
    CAST(MAX(start_time) AS TIMESTAMP) AS completed_at,
    DATE_TRUNC('WEEK', start_time) AS period_start,
    DATE_ADD(DATE_TRUNC('WEEK', start_time), 6) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.query.history
  WHERE executed_by IS NOT NULL AND executed_by != ''
    AND statement_type IN ('SELECT', 'INSERT', 'MERGE', 'CREATE', 'ALTER')
    -- Human ad-hoc queries only: exclude queries issued by a job or pipeline.
    -- Check the inner id — the job_info/pipeline_info struct itself is usually
    -- present (non-null) even for interactive queries; only the id is populated
    -- when the query actually originated from a job run or pipeline update.
    AND query_source.job_info.job_id IS NULL
    AND query_source.pipeline_info.pipeline_id IS NULL
    AND execution_status = 'FINISHED'
    AND start_time >= DATE_SUB(CURRENT_DATE(), 30)
  GROUP BY executed_by, DATE_TRUNC('WEEK', start_time)
  HAVING COUNT(*) >= 200
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id AND target.period_start = source.period_start
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Power Analyst")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Alert Creator (150 pts)
# MAGIC Create a SQL Alert with a schedule.

# COMMAND ----------

try:
    spark.sql(f"""
    MERGE INTO {tbl('mission_completions')} AS target
    USING (
      SELECT
        user_identity.email AS user_id,
        'alert_creator' AS mission_id,
        'Alert Creator' AS mission_name,
        150 AS points_awarded,
        MIN(event_time) AS completed_at,
        CAST(MIN(event_time) AS DATE) AS period_start,
        CAST(MIN(event_time) AS DATE) AS period_end,
        CAST('{NOW}' AS TIMESTAMP) AS scored_at
      FROM system.access.audit
      WHERE action_name IN ('createAlert', 'createLegacyAlert')
        AND response.status_code = 200
        AND user_identity.email IS NOT NULL AND user_identity.email != ''
        AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
      GROUP BY user_identity.email
    ) AS source
    ON target.user_id = source.user_id AND target.mission_id = source.mission_id
    WHEN NOT MATCHED THEN INSERT *
    """)
    print("Mission scored: Alert Creator")
except Exception as e:
    print(f"Mission skipped: Alert Creator ({e})")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Model Deployer (300 pts)
# MAGIC Deploy a model to a serving endpoint.

# COMMAND ----------

try:
    spark.sql(f"""
    MERGE INTO {tbl('mission_completions')} AS target
    USING (
      SELECT
        user_identity.email AS user_id,
        'model_deployer' AS mission_id,
        'Model Deployer' AS mission_name,
        300 AS points_awarded,
        MIN(event_time) AS completed_at,
        CAST(MIN(event_time) AS DATE) AS period_start,
        CAST(MIN(event_time) AS DATE) AS period_end,
        CAST('{NOW}' AS TIMESTAMP) AS scored_at
      FROM system.access.audit
      WHERE action_name IN ('createServingEndpoint', 'create_serving_endpoint')
        AND response.status_code = 200
        AND user_identity.email IS NOT NULL AND user_identity.email != ''
        AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
      GROUP BY user_identity.email
    ) AS source
    ON target.user_id = source.user_id AND target.mission_id = source.mission_id
    WHEN NOT MATCHED THEN INSERT *
    """)
    print("Mission scored: Model Deployer")
except Exception as e:
    print(f"Mission skipped: Model Deployer ({e})")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: AI Function Builder (250 pts)
# MAGIC Use ai_query() in a SQL statement.

# COMMAND ----------

try:
    spark.sql(f"""
    MERGE INTO {tbl('mission_completions')} AS target
    USING (
      SELECT
        executed_by AS user_id,
        'ai_function_builder' AS mission_id,
        'AI Function Builder' AS mission_name,
        250 AS points_awarded,
        CAST(MIN(start_time) AS TIMESTAMP) AS completed_at,
        CAST(MIN(start_time) AS DATE) AS period_start,
        CAST(MIN(start_time) AS DATE) AS period_end,
        CAST('{NOW}' AS TIMESTAMP) AS scored_at
      FROM system.query.history
      WHERE LOWER(statement_text) LIKE '%ai_query%'
        AND executed_by IS NOT NULL AND executed_by != ''
        AND execution_status = 'FINISHED'
        AND start_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
      GROUP BY executed_by
    ) AS source
    ON target.user_id = source.user_id AND target.mission_id = source.mission_id
    WHEN NOT MATCHED THEN INSERT *
    """)
    print("Mission scored: AI Function Builder")
except Exception as e:
    print(f"Mission skipped: AI Function Builder ({e})")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: MLflow Experimenter (150 pts)
# MAGIC Log 10+ MLflow experiment runs.

# COMMAND ----------

try:
    spark.sql(f"""
    MERGE INTO {tbl('mission_completions')} AS target
    USING (
      SELECT
        user_identity.email AS user_id,
        'mlflow_experimenter' AS mission_id,
        'MLflow Experimenter' AS mission_name,
        150 AS points_awarded,
        CAST(MAX(event_time) AS TIMESTAMP) AS completed_at,
        CAST(MIN(event_time) AS DATE) AS period_start,
        CAST(MAX(event_time) AS DATE) AS period_end,
        CAST('{NOW}' AS TIMESTAMP) AS scored_at
      FROM system.access.audit
      -- MLflow experiment run logging is audited as
      -- service_name='mlflowExperiment', action_name='createLoggedModel'.
      -- (The old 'createRun'/'mlflowCreateRun' names are not emitted, so the
      -- mission never fired — verified 0 rows in system.access.audit.)
      WHERE service_name = 'mlflowExperiment'
        AND action_name = 'createLoggedModel'
        AND response.status_code = 200
        AND user_identity.email IS NOT NULL AND user_identity.email != ''
        AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
      GROUP BY user_identity.email
      HAVING COUNT(*) >= 10
    ) AS source
    ON target.user_id = source.user_id AND target.mission_id = source.mission_id
    WHEN NOT MATCHED THEN INSERT *
    """)
    print("Mission scored: MLflow Experimenter")
except Exception as e:
    print(f"Mission skipped: MLflow Experimenter ({e})")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Vector Search Pioneer (200 pts)
# MAGIC Create a Vector Search (Databricks AI Search) index.
# MAGIC Audited as service_name='vectorSearch', action_name='createVectorIndex'.

# COMMAND ----------

try:
    spark.sql(f"""
    MERGE INTO {tbl('mission_completions')} AS target
    USING (
      SELECT
        user_identity.email AS user_id,
        'vector_search_pioneer' AS mission_id,
        'Vector Search Pioneer' AS mission_name,
        200 AS points_awarded,
        MIN(event_time) AS completed_at,
        CAST(MIN(event_time) AS DATE) AS period_start,
        CAST(MIN(event_time) AS DATE) AS period_end,
        CAST('{NOW}' AS TIMESTAMP) AS scored_at
      FROM system.access.audit
      WHERE service_name = 'vectorSearch'
        AND action_name = 'createVectorIndex'
        AND response.status_code = 200
        AND user_identity.email IS NOT NULL AND user_identity.email != ''
        AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
      GROUP BY user_identity.email
    ) AS source
    ON target.user_id = source.user_id AND target.mission_id = source.mission_id
    WHEN NOT MATCHED THEN INSERT *
    """)
    print("Mission scored: Vector Search Pioneer")
except Exception as e:
    print(f"Mission skipped: Vector Search Pioneer ({e})")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Liquid Clustering Adopter (200 pts)
# MAGIC Enable Liquid Clustering on a table — detected by a CLUSTER BY clause in
# MAGIC the user's executed CREATE/ALTER SQL (interactive, not job-issued).

# COMMAND ----------

try:
    spark.sql(f"""
    MERGE INTO {tbl('mission_completions')} AS target
    USING (
      SELECT
        executed_by AS user_id,
        'liquid_clustering' AS mission_id,
        'Liquid Clustering Adopter' AS mission_name,
        200 AS points_awarded,
        CAST(MIN(start_time) AS TIMESTAMP) AS completed_at,
        CAST(MIN(start_time) AS DATE) AS period_start,
        CAST(MIN(start_time) AS DATE) AS period_end,
        CAST('{NOW}' AS TIMESTAMP) AS scored_at
      FROM system.query.history
      WHERE executed_by IS NOT NULL AND executed_by != ''
        AND LOWER(statement_text) LIKE '%cluster by%'
        AND statement_type IN ('CREATE', 'ALTER')
        AND query_source.job_info.job_id IS NULL
        AND query_source.pipeline_info.pipeline_id IS NULL
        AND start_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
      GROUP BY executed_by
    ) AS source
    ON target.user_id = source.user_id AND target.mission_id = source.mission_id
    WHEN NOT MATCHED THEN INSERT *
    """)
    print("Mission scored: Liquid Clustering Adopter")
except Exception as e:
    print(f"Mission skipped: Liquid Clustering Adopter ({e})")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Unity Catalog Publisher (150 pts)
# MAGIC Share a table across schemas — detected by a UC permission grant
# MAGIC (service_name='unityCatalog', action_name='updatePermissions').

# COMMAND ----------

try:
    spark.sql(f"""
    MERGE INTO {tbl('mission_completions')} AS target
    USING (
      SELECT
        user_identity.email AS user_id,
        'uc_publisher' AS mission_id,
        'Unity Catalog Publisher' AS mission_name,
        150 AS points_awarded,
        MIN(event_time) AS completed_at,
        CAST(MIN(event_time) AS DATE) AS period_start,
        CAST(MIN(event_time) AS DATE) AS period_end,
        CAST('{NOW}' AS TIMESTAMP) AS scored_at
      FROM system.access.audit
      WHERE service_name = 'unityCatalog'
        AND action_name = 'updatePermissions'
        AND response.status_code = 200
        AND user_identity.email IS NOT NULL AND user_identity.email != ''
        -- Exclude the scoring pipeline's OWN auto-grant to the app service
        -- principal (it grants on the Quest catalog/schema every run and would
        -- otherwise award uc_publisher to whoever runs this notebook).
        AND COALESCE(request_params['securable_full_name'], '') NOT LIKE '{CATALOG}.{SCHEMA}%'
        AND COALESCE(request_params['securable_full_name'], '') <> '{CATALOG}'
        AND event_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
      GROUP BY user_identity.email
    ) AS source
    ON target.user_id = source.user_id AND target.mission_id = source.mission_id
    WHEN NOT MATCHED THEN INSERT *
    """)
    print("Mission scored: Unity Catalog Publisher")
except Exception as e:
    print(f"Mission skipped: Unity Catalog Publisher ({e})")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Stream Starter (250 pts)
# MAGIC Run a Structured Streaming job. Structured Streaming has no single system
# MAGIC table signal, so detect either: (a) a user whose executed SQL uses the
# MAGIC streaming APIs (readStream/writeStream), or (b) the creator of a job with a
# MAGIC CONTINUOUS trigger. Union of both, one-time award.

# COMMAND ----------

try:
    spark.sql(f"""
    MERGE INTO {tbl('mission_completions')} AS target
    USING (
      SELECT
        user_id,
        'stream_starter' AS mission_id,
        'Stream Starter' AS mission_name,
        250 AS points_awarded,
        MIN(completed_at) AS completed_at,
        CAST(MIN(completed_at) AS DATE) AS period_start,
        CAST(MIN(completed_at) AS DATE) AS period_end,
        CAST('{NOW}' AS TIMESTAMP) AS scored_at
      FROM (
        SELECT executed_by AS user_id, start_time AS completed_at
        FROM system.query.history
        WHERE executed_by IS NOT NULL AND executed_by != ''
          AND (LOWER(statement_text) LIKE '%readstream%' OR LOWER(statement_text) LIKE '%writestream%')
          AND start_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
        UNION ALL
        SELECT creator_user_name AS user_id, change_time AS completed_at
        FROM system.lakeflow.jobs
        WHERE creator_user_name IS NOT NULL AND creator_user_name != ''
          AND trigger_type = 'CONTINUOUS'
          AND delete_time IS NULL
          AND change_time >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
      )
      GROUP BY user_id
    ) AS source
    ON target.user_id = source.user_id AND target.mission_id = source.mission_id
    WHEN NOT MATCHED THEN INSERT *
    """)
    print("Mission scored: Stream Starter")
except Exception as e:
    print(f"Mission skipped: Stream Starter ({e})")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Consumption Missions: product-specific usage

# COMMAND ----------

# Product-specific consumption missions (repeatable monthly).
# Only products where a HUMAN drives interactive consumption belong here:
#   * SQL warehouse — a person running queries in the editor / dashboards.
#   * Model Serving — a person exercising an endpoint they own.
# The INTERACTIVE_USAGE filter drops scheduled job/pipeline compute so an
# on-leave owner's cron can't keep earning.
#
# NOTE: the old "Job Runner" (JOBS DBUs) and "Pipeline Operator" (DLT DBUs)
# consumption missions were removed. Jobs/DLT compute is automated by
# definition (every row carries a job_id / dlt_pipeline_id), so those missions
# rewarded "each time the workload runs" — exactly what we don't want. Setting
# up those workloads is already a one-time award (Job Creator, Pipeline
# Builder), which is where the "reward first-time setup" credit belongs.
# tuple: (id, name, points, product, dbu_threshold, require_interactive_month)
# require_interactive_month=True means the user must ALSO have genuine interactive
# activity (INTERACTIVE_PRODUCTS) in the SAME month — this stops an always-on
# Model Serving endpoint from earning ml_practitioner every month while its owner
# does nothing. SQL Analyst is already interactive SQL usage, so it doesn't need
# the extra gate.
PRODUCT_MISSIONS = [
    ("sql_analyst", "SQL Analyst", 100, "SQL", 50, False),
    ("ml_practitioner", "ML Practitioner", 150, "MODEL_SERVING", 1, True),
]

# These missions exclude *scheduled job* compute (job_id) but NOT dlt_pipeline_id.
# Reason: serverless SQL warehouse billing rows spuriously carry a dlt_pipeline_id
# even for ordinary interactive editor/dashboard queries, so the full
# INTERACTIVE_USAGE predicate (which also excludes dlt_pipeline_id) wrongly zeroed
# out SQL Analyst. Excluding only job_id is the correct human-vs-automation split
# for these product-consumption missions (verified: 0 -> 65 SQL Analyst qualifiers).
PRODUCT_INTERACTIVE = "usage_metadata.job_id IS NULL"
_prod_allow = ", ".join(f"'{p}'" for p in INTERACTIVE_PRODUCTS)

for m_id, m_name, m_pts, product_filter, dbu_threshold, require_interactive in PRODUCT_MISSIONS:
    try:
        interactive_gate = ""
        if require_interactive:
            interactive_gate = f"""
            AND EXISTS (
              SELECT 1 FROM system.billing.usage i
              WHERE i.identity_metadata.run_as = u.identity_metadata.run_as
                AND i.usage_quantity > 0
                AND i.usage_metadata.job_id IS NULL AND i.usage_metadata.dlt_pipeline_id IS NULL
                AND i.billing_origin_product IN ({_prod_allow})
                AND DATE_TRUNC('MONTH', i.usage_date) = DATE_TRUNC('MONTH', u.usage_date)
            )"""
        spark.sql(f"""
        MERGE INTO {tbl('mission_completions')} AS target
        USING (
          SELECT
            identity_metadata.run_as AS user_id,
            '{m_id}' AS mission_id,
            '{m_name}' AS mission_name,
            {m_pts} AS points_awarded,
            CAST(MAX(usage_date) AS TIMESTAMP) AS completed_at,
            DATE_TRUNC('MONTH', usage_date) AS period_start,
            LAST_DAY(usage_date) AS period_end,
            CAST('{NOW}' AS TIMESTAMP) AS scored_at
          FROM system.billing.usage u
          WHERE identity_metadata.run_as IS NOT NULL AND identity_metadata.run_as != ''
            AND usage_quantity > 0
            AND {PRODUCT_INTERACTIVE}
            AND billing_origin_product = '{product_filter}'
            AND usage_date >= DATE_TRUNC('MONTH', DATE_SUB(CURRENT_DATE(), 60))
            {interactive_gate}
          GROUP BY identity_metadata.run_as, DATE_TRUNC('MONTH', usage_date), LAST_DAY(usage_date)
          HAVING SUM(usage_quantity) >= {dbu_threshold}
        ) AS source
        ON target.user_id = source.user_id AND target.mission_id = source.mission_id AND target.period_start = source.period_start
        WHEN NOT MATCHED THEN INSERT *
        """)
        print(f"Mission scored: {m_name}")
    except Exception as e:
        print(f"Mission skipped: {m_name} ({e})")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Consumption Points: Weekly DBU-based points (1 pt per 10 DBUs)
# MAGIC
# MAGIC Recomputed from scratch every run (DELETE + INSERT), NOT insert-if-missing.
# MAGIC These points are a pure rolling aggregate over the last 90 days of billing
# MAGIC usage, so the current run's filtered logic must be authoritative. An
# MAGIC insert-only MERGE would leave behind rows scored under older/looser logic,
# MAGIC which would keep inflating the leaderboard forever. Deleting the prior
# MAGIC consumption rows first guarantees the change actually takes effect on
# MAGIC re-score. Mission-completion points are append-only and handled separately.
# MAGIC
# MAGIC Only INTERACTIVE_PRODUCTS count (human-driven compute), and each user's
# MAGIC weekly points are capped at WEEKLY_CONSUMPTION_POINT_CAP so raw compute
# MAGIC volume — e.g. an always-on model-serving endpoint — can never dominate the
# MAGIC human adoption leaderboard.

# COMMAND ----------

_prod_in = ", ".join(f"'{p}'" for p in INTERACTIVE_PRODUCTS)

# Clear prior consumption rows so they are rebuilt under the current filter.
spark.sql(f"""
DELETE FROM {tbl('user_points_fact')}
WHERE event_type = 'consumption' AND mission_id = 'weekly_dbu'
""")

spark.sql(f"""
INSERT INTO {tbl('user_points_fact')}
SELECT
  user_id,
  'consumption' AS event_type,
  'weekly_dbu' AS mission_id,
  LEAST(raw_points, {WEEKLY_CONSUMPTION_POINT_CAP}) AS points,
  CASE WHEN raw_points > {WEEKLY_CONSUMPTION_POINT_CAP}
       THEN CONCAT('Weekly compute: ', ROUND(dbus, 1), ' DBUs (capped at {WEEKLY_CONSUMPTION_POINT_CAP})')
       ELSE CONCAT('Weekly compute: ', ROUND(dbus, 1), ' DBUs') END AS reason,
  event_timestamp,
  CAST('{NOW}' AS TIMESTAMP) AS scored_at
FROM (
  SELECT
    identity_metadata.run_as AS user_id,
    SUM(usage_quantity) AS dbus,
    CAST(FLOOR(SUM(usage_quantity) / 10) AS INT) AS raw_points,
    CAST(MAX(usage_date) AS TIMESTAMP) AS event_timestamp
  FROM system.billing.usage
  WHERE identity_metadata.run_as IS NOT NULL AND identity_metadata.run_as != ''
    AND usage_quantity > 0
    AND {INTERACTIVE_USAGE}
    AND billing_origin_product IN ({_prod_in})
    AND usage_date >= DATE_SUB(CURRENT_DATE(), 90)
  GROUP BY identity_metadata.run_as, DATE_TRUNC('WEEK', usage_date)
  HAVING SUM(usage_quantity) >= 10
)
""")

print("Consumption points recomputed (interactive products only, per-week capped)")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Engagement: Daily Driver (400 pts) and Cross-Product Champion (500 pts)

# COMMAND ----------

# Daily Driver: 20+ active days in 30 days
spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    identity_metadata.run_as AS user_id,
    'daily_driver' AS mission_id,
    'Daily Driver' AS mission_name,
    400 AS points_awarded,
    CAST(MAX(usage_date) AS TIMESTAMP) AS completed_at,
    DATE_SUB(CURRENT_DATE(), 30) AS period_start,
    CURRENT_DATE() AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.billing.usage
  WHERE identity_metadata.run_as IS NOT NULL AND identity_metadata.run_as != ''
    AND usage_quantity > 0
    AND {INTERACTIVE_USAGE}
    AND usage_date >= DATE_SUB(CURRENT_DATE(), 30)
  GROUP BY identity_metadata.run_as
  HAVING COUNT(DISTINCT usage_date) >= 20
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id AND target.period_start = source.period_start
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Daily Driver")

# Cross-Product Champion: 6+ distinct products in a month
spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    identity_metadata.run_as AS user_id,
    'cross_product_champion' AS mission_id,
    'Cross-Product Champion' AS mission_name,
    500 AS points_awarded,
    CAST(MAX(usage_date) AS TIMESTAMP) AS completed_at,
    DATE_TRUNC('MONTH', usage_date) AS period_start,
    LAST_DAY(usage_date) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.billing.usage
  WHERE identity_metadata.run_as IS NOT NULL AND identity_metadata.run_as != ''
    AND usage_quantity > 0
    AND {INTERACTIVE_USAGE}
    AND usage_date >= DATE_TRUNC('MONTH', DATE_SUB(CURRENT_DATE(), 60))
  GROUP BY identity_metadata.run_as, DATE_TRUNC('MONTH', usage_date), LAST_DAY(usage_date)
  HAVING COUNT(DISTINCT billing_origin_product) >= 6
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id AND target.period_start = source.period_start
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Cross-Product Champion")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2b: Score Get Started training missions (from the completions feed)
# MAGIC Databricks Academy course completions do NOT live in Unity Catalog system
# MAGIC tables — Academy is a separate platform. Completions reach Quest through the
# MAGIC `training_completions` Delta feed, populated two ways:
# MAGIC   - `self_attested` — a user ticked the course complete in the app (honor
# MAGIC     system). The app ALSO writes the award to the serving tables immediately
# MAGIC     for instant UX; this block RE-DERIVES the same rows idempotently so the
# MAGIC     4-hourly rebuild never drops or double-counts a tick.
# MAGIC   - `classroom` — instructor-led completions optionally landed by an admin export.
# MAGIC
# MAGIC Each Get Started course is one one-time mission (250 pts), matched on the
# MAGIC course's id set (free self-paced id + instructor-led + language variants, so
# MAGIC any edition counts once). A one-time "Databricks Learner" bonus (500 pts)
# MAGIC fires once a user has completed 2+ DISTINCT Get Started courses.
# MAGIC
# MAGIC `user_id` in the feed MUST match the workspace identity Quest keys on (the
# MAGIC user's email / `identity_metadata.run_as`). Academy is a separate login; if the
# MAGIC emails don't match, points can't be awarded — the matched/dropped counts are
# MAGIC logged below so a mismatch is diagnosable rather than silent.

# COMMAND ----------

# Create the completions feed table if it doesn't exist yet, so a deploy never
# breaks when no feed has been landed. The 11 missions below simply stay unearned
# until the table has classroom rows whose user_id matches a Quest user.
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('training_completions')} (
  user_id STRING,
  course_id STRING,
  course_name STRING,
  course_type STRING,
  completed_at TIMESTAMP
)
USING DELTA
""")

# Canonical English "Get Started" ILT series. Each mission's course_ids list holds
# the English course id PLUS its language-variant ids (all course_type='classroom'
# in the Academy catalog), so every edition of a course maps to the same mission.
# Verified live against customer-academy.databricks.com on 2026-07-16/17.
GET_STARTED_COURSES = [
    # course_ids include the instructor-led + language-variant ids AND the free
    # self-paced id the tick-box attests against (first id in each list = the free
    # self-paced course the mission's doc_url/link points at).
    {"mission_id": "gs_data_engineering",      "mission_name": "Get Started: Data Engineering",      "course_ids": ["2469", "1511", "2438", "2439", "2496", "3908", "3933"]},
    {"mission_id": "gs_machine_learning",      "mission_name": "Get Started: Machine Learning",      "course_ids": ["2460", "1512", "3518", "3519", "3521"]},
    {"mission_id": "gs_generative_ai",         "mission_name": "Get Started: Generative AI",         "course_ids": ["2724", "3514", "3665", "3667", "3669", "3751"]},
    {"mission_id": "gs_sql_analytics_bi",      "mission_name": "Get Started: SQL Analytics & BI",    "course_ids": ["3347", "1513", "3393", "3394", "3395", "3728", "3760"]},
    {"mission_id": "gs_data_warehousing",      "mission_name": "Get Started: Data Warehousing",      "course_ids": ["3603", "3553", "4182", "4196", "4210"]},
    {"mission_id": "gs_platform_admin",        "mission_name": "Get Started: Platform Administration", "course_ids": ["2453", "1509", "4471", "4499", "4535"]},
    {"mission_id": "gs_data_governance",       "mission_name": "Get Started: Data Governance",       "course_ids": ["4677", "4653", "4829", "4831"]},
    {"mission_id": "gs_lakebase",              "mission_name": "Get Started: Lakebase",              "course_ids": ["5081", "5082", "5400", "5448", "5454", "5673", "5674"]},
    {"mission_id": "gs_lakehouse_architecture", "mission_name": "Get Started: Lakehouse Architecture", "course_ids": ["3509", "3532", "4276", "4292", "4298"]},
    {"mission_id": "gs_ai_agents",             "mission_name": "Get Started: AI Agents",             "course_ids": ["4459"]},
]
GET_STARTED_POINTS = 250
LEARNER_BONUS_POINTS = 500
LEARNER_THRESHOLD = 2

# All Get Started mission ids (the 10 courses + the Learner bonus). These belong to
# the "Getting Started" category — used below to keep the Full Stack badge's
# mission_id -> category map in sync with MISSION_DEFINITIONS in app/main.py.
GET_STARTED_MISSION_IDS = [c["mission_id"] for c in GET_STARTED_COURSES] + ["databricks_learner"]

# Scoring counts two kinds of completion rows:
#   'classroom'     — instructor-led completions landed by an admin export
#   'self_attested' — user ticked the course as complete in the app (honor system)
# The app writes 'self_attested' rows AND the serving-table award directly (instant
# UX); this reconciliation reproduces the same idempotent rows so the 4-hourly
# rebuild never drops or double-counts a tick. SP-looking identities are swept
# later, but we exclude them here too so the matched/dropped counts are honest.
_TC_HUMAN = (
    "user_id IS NOT NULL AND user_id != '' "
    "AND user_id NOT LIKE '%service-principal%' "
    "AND user_id NOT LIKE '%ServicePrincipal%'"
)
# completed_at must be present: a NULL timestamp becomes the completion/event
# timestamp downstream, and since NULL != NULL in SQL, Step 3's user_points_fact
# MERGE would treat every run as "not matched" and re-insert the points on every
# 4-hourly run. Requiring a non-NULL timestamp keeps awards idempotent.
_TC_CLASSROOM = f"course_type IN ('classroom', 'self_attested') AND completed_at IS NOT NULL AND {_TC_HUMAN}"

# Diagnostics: how many feed users match a known Quest user vs. are dropped.
try:
    _feed_users = spark.sql(
        f"SELECT COUNT(DISTINCT user_id) AS c FROM {tbl('training_completions')} WHERE {_TC_CLASSROOM}"
    ).first()["c"]
    _matched_users = spark.sql(f"""
        SELECT COUNT(DISTINCT tc.user_id) AS c
        FROM {tbl('training_completions')} tc
        WHERE {_TC_CLASSROOM}
          AND tc.user_id IN (SELECT DISTINCT user_id FROM {tbl('mission_completions')})
    """).first()["c"]
    print(
        f"Training feed: {_feed_users} classroom user(s); "
        f"{_matched_users} already known to Quest, "
        f"{_feed_users - _matched_users} not yet seen elsewhere "
        f"(they still score training missions; they just have no other activity yet)."
    )
    # The service-principal sweep later DELETEs any row whose user_id doesn't look
    # like an email (NOT LIKE '%@%'). A feed that uses Academy usernames instead of
    # workspace emails would score here and then be silently swept. Warn loudly so
    # an identity-format mismatch is obvious rather than a silent zero.
    _bad_id_users = spark.sql(
        f"SELECT COUNT(DISTINCT user_id) AS c FROM {tbl('training_completions')} "
        f"WHERE {_TC_CLASSROOM} AND user_id NOT LIKE '%@%'"
    ).first()["c"]
    if _bad_id_users:
        print(
            f"  WARNING: {_bad_id_users} training feed user(s) have a non-email user_id "
            f"(no '@'). Their training points will be scored and then REMOVED by the "
            f"service-principal sweep. The feed's user_id must be the workspace email."
        )
except Exception as _e:
    print(f"Training feed diagnostics skipped: {_e}")

# One MERGE per Get Started course. completed_at = earliest completion for that
# user across any edition of the course. Idempotent on (user_id, mission_id).
for _course in GET_STARTED_COURSES:
    _ids_in = ", ".join(f"'{c}'" for c in _course["course_ids"])
    spark.sql(f"""
    MERGE INTO {tbl('mission_completions')} AS target
    USING (
      SELECT
        user_id,
        '{_course["mission_id"]}' AS mission_id,
        '{_course["mission_name"]}' AS mission_name,
        {GET_STARTED_POINTS} AS points_awarded,
        MIN(completed_at) AS completed_at,
        CAST(MIN(completed_at) AS DATE) AS period_start,
        CAST(MIN(completed_at) AS DATE) AS period_end,
        CAST('{NOW}' AS TIMESTAMP) AS scored_at
      FROM {tbl('training_completions')}
      WHERE {_TC_CLASSROOM}
        AND course_id IN ({_ids_in})
      GROUP BY user_id
    ) AS source
    ON target.user_id = source.user_id AND target.mission_id = source.mission_id
    WHEN NOT MATCHED THEN INSERT *
    """)
    print(f"Mission scored: {_course['mission_name']}")

# Databricks Learner bonus: 2+ DISTINCT Get Started courses completed. Distinctness
# is by mission_id (i.e. by course), so two language-variant rows of the SAME course
# count as one. completed_at = the moment the 2nd distinct course was completed.
_learner_when = "\n            ".join(
    f"WHEN course_id IN ({', '.join(repr(c) for c in _c['course_ids'])}) THEN '{_c['mission_id']}'"
    for _c in GET_STARTED_COURSES
)
spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_id,
    'databricks_learner' AS mission_id,
    'Databricks Learner' AS mission_name,
    {LEARNER_BONUS_POINTS} AS points_awarded,
    CAST(nth_completion AS TIMESTAMP) AS completed_at,
    CAST(nth_completion AS DATE) AS period_start,
    CAST(nth_completion AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM (
    SELECT
      user_id,
      COUNT(*) AS distinct_courses,
      -- the completion date of the Nth (threshold-th) distinct course. Use
      -- try_element_at (1-indexed) NOT the [] subscript: Spark evaluates this
      -- projection for EVERY group before HAVING prunes, so a user with a single
      -- Get Started course yields a 1-element array and a fixed [LEARNER_THRESHOLD-1]
      -- subscript throws INVALID_ARRAY_INDEX, failing the whole scoring run.
      -- try_element_at returns NULL out-of-range; the HAVING below then drops those
      -- sub-threshold users, so qualifying rows always have a non-NULL nth_completion.
      try_element_at(SORT_ARRAY(COLLECT_LIST(first_completed_at)), {LEARNER_THRESHOLD}) AS nth_completion
    FROM (
      SELECT
        user_id,
        CASE
            {_learner_when}
        END AS course_mission_id,
        MIN(completed_at) AS first_completed_at
      FROM {tbl('training_completions')}
      WHERE {_TC_CLASSROOM}
      GROUP BY user_id,
        CASE
            {_learner_when}
        END
    )
    WHERE course_mission_id IS NOT NULL
    GROUP BY user_id
    HAVING COUNT(*) >= {LEARNER_THRESHOLD}
  )
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")
print("Mission scored: Databricks Learner (bonus)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3: Generate User Points Fact Table
# MAGIC Consolidate all mission completions into the points fact table.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('user_points_fact')} AS target
USING (
  SELECT
    user_id,
    'mission_completion' AS event_type,
    mission_id,
    points_awarded AS points,
    CONCAT('Completed mission: ', mission_name) AS reason,
    completed_at AS event_timestamp,
    scored_at
  FROM {tbl('mission_completions')}
) AS source
ON target.user_id = source.user_id
  AND target.mission_id = source.mission_id
  AND target.event_timestamp = source.event_timestamp
WHEN NOT MATCHED THEN INSERT *
""")

print("User points fact table updated.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4: Compute Activity Streaks

# COMMAND ----------

streaks_df = spark.sql(f"""
WITH daily_activity AS (
  SELECT
    identity_metadata.run_as AS user_id,
    CAST(usage_date AS DATE) AS activity_date
  FROM system.billing.usage
  WHERE identity_metadata.run_as IS NOT NULL
    AND identity_metadata.run_as != ''
    AND usage_quantity > 0
    AND {INTERACTIVE_USAGE}
    AND usage_date >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
  GROUP BY identity_metadata.run_as, usage_date
),
streak_groups AS (
  SELECT
    user_id,
    activity_date,
    DATE_SUB(activity_date, CAST(ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY activity_date) AS INT)) AS streak_group
  FROM daily_activity
),
streak_lengths AS (
  SELECT
    user_id,
    streak_group,
    COUNT(*) AS streak_days,
    MAX(activity_date) AS streak_end
  FROM streak_groups
  GROUP BY user_id, streak_group
),
-- One row per user: the all-time longest streak (max_streak) and the length of
-- the MOST RECENT streak. current_streak only counts if that most-recent streak
-- is still live (ended yesterday or today) — otherwise the streak has lapsed and
-- resets to 0. The previous version returned the most-recent streak length
-- regardless of recency, so lapsed streaks never reset (~1,500 stale rows).
ranked AS (
  SELECT
    user_id,
    streak_days,
    streak_end,
    MAX(streak_days) OVER (PARTITION BY user_id) AS max_streak,
    ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY streak_end DESC) AS rn
  FROM streak_lengths
)
SELECT
  user_id,
  max_streak,
  CASE WHEN streak_end >= DATE_SUB(CURRENT_DATE(), 1) THEN streak_days ELSE 0 END AS current_streak
FROM ranked
WHERE rn = 1
""")

streaks_df.createOrReplaceTempView("user_streaks")
print("Streak data computed.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Remove non-human principals (service principals)
# MAGIC System tables attribute job/automation activity to service principals, whose
# MAGIC ids are bare application UUIDs (no email). Quest is a human adoption game, so
# MAGIC before building the profile/leaderboard we sweep service principals out of the
# MAGIC scored fact tables, keeping only real users (an email-shaped user_id). Doing it
# MAGIC here means the leaderboard ranks are computed over humans only, and it runs
# MAGIC every cycle so the data stays clean after each re-score.

# COMMAND ----------

_human = "user_id IS NULL OR user_id NOT LIKE '%@%'"
for _t in ["mission_completions", "user_points_fact"]:
    _n = spark.sql(f"SELECT COUNT(*) AS c FROM {tbl(_t)} WHERE {_human}").first()["c"]
    if _n:
        spark.sql(f"DELETE FROM {tbl(_t)} WHERE {_human}")
        print(f"  {_t}: removed {_n} service-principal rows")
print("Service-principal sweep complete — profile/leaderboard/badges build from human users only.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Retire removed missions from the fact tables
# MAGIC The job_runner / dlt_operator "consume DBUs" missions were removed (Jobs/DLT
# MAGIC compute is automated by definition — those rewarded every scheduled run, not a
# MAGIC person). The dbu_100 / dbu_1k / dbu_10k / dbu_100k "DBU Club" missions were
# MAGIC also removed in an earlier revision. MERGE never deletes, so completions
# MAGIC written by earlier runs would keep contributing points. Sweep them all BEFORE
# MAGIC the profile/leaderboard are built so the retired points don't linger in totals.

# COMMAND ----------

RETIRED_MISSIONS = ("job_runner", "dlt_operator", "dbu_100", "dbu_1k", "dbu_10k", "dbu_100k")
_retired_in = ", ".join(f"'{m}'" for m in RETIRED_MISSIONS)
for _t in ["mission_completions", "user_points_fact"]:
    _n = spark.sql(
        f"SELECT COUNT(*) AS c FROM {tbl(_t)} WHERE mission_id IN ({_retired_in})"
    ).first()["c"]
    if _n:
        spark.sql(f"DELETE FROM {tbl(_t)} WHERE mission_id IN ({_retired_in})")
        print(f"  {_t}: removed {_n} rows for retired missions ({', '.join(RETIRED_MISSIONS)})")
print("Retired-mission sweep complete.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Enforce Data Explorer / Power Analyst tiering
# MAGIC data_explorer (50-199 queries) and power_analyst (200+) are scored by
# MAGIC separate MERGEs that each read system.query.history at slightly different
# MAGIC times. A user hovering near 200 can be under the threshold when
# MAGIC data_explorer runs and over it when power_analyst runs, so both fire for the
# MAGIC same week. Power Analyst is the higher tier and supersedes Data Explorer:
# MAGIC delete any data_explorer completion that shares a user+period with a
# MAGIC power_analyst completion, so a week never pays both.

# COMMAND ----------

_de_dupe = spark.sql(f"""
  SELECT COUNT(*) AS c FROM {tbl('mission_completions')} de
  WHERE de.mission_id = 'data_explorer'
    AND EXISTS (
      SELECT 1 FROM {tbl('mission_completions')} pa
      WHERE pa.mission_id = 'power_analyst'
        AND pa.user_id = de.user_id AND pa.period_start = de.period_start
    )
""").first()["c"]
if _de_dupe:
    spark.sql(f"""
      DELETE FROM {tbl('mission_completions')}
      WHERE mission_id = 'data_explorer'
        AND EXISTS (
          SELECT 1 FROM {tbl('mission_completions')} pa
          WHERE pa.mission_id = 'power_analyst'
            AND pa.user_id = {tbl('mission_completions')}.user_id
            AND pa.period_start = {tbl('mission_completions')}.period_start
        )
    """)
    spark.sql(f"""
      DELETE FROM {tbl('user_points_fact')}
      WHERE mission_id = 'data_explorer'
        AND event_type = 'mission_completion'
        AND EXISTS (
          SELECT 1 FROM {tbl('mission_completions')} pa
          WHERE pa.mission_id = 'power_analyst'
            AND pa.user_id = {tbl('user_points_fact')}.user_id
            AND pa.period_start = DATE_TRUNC('WEEK', {tbl('user_points_fact')}.event_timestamp)
        )
    """)
    print(f"Tiering sweep: removed {_de_dupe} data_explorer rows superseded by power_analyst.")
else:
    print("Tiering sweep: no data_explorer/power_analyst overlaps.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5: Compute Product Breadth

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW user_products AS
SELECT
  identity_metadata.run_as AS user_id,
  COUNT(DISTINCT billing_origin_product) AS distinct_products
FROM system.billing.usage
WHERE identity_metadata.run_as IS NOT NULL
  AND identity_metadata.run_as != ''
  AND usage_quantity > 0
  AND {INTERACTIVE_USAGE}
  AND usage_date >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
GROUP BY identity_metadata.run_as
""")

print("Product breadth computed.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6: Build User Profile Snapshots

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW user_point_totals AS
SELECT
  COALESCE(m.user_id, c.user_id) AS user_id,
  COALESCE(m.mission_points, 0) + COALESCE(c.consumption_points, 0) AS total_points,
  COALESCE(m.missions_completed, 0) AS missions_completed
FROM (
  SELECT user_id, SUM(points_awarded) AS mission_points, COUNT(*) AS missions_completed
  FROM {tbl('mission_completions')}
  GROUP BY user_id
) m
FULL OUTER JOIN (
  SELECT user_id, SUM(points) AS consumption_points
  FROM {tbl('user_points_fact')}
  WHERE event_type = 'consumption'
  GROUP BY user_id
) c ON m.user_id = c.user_id
""")

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW user_activity_dates AS
SELECT
  identity_metadata.run_as AS user_id,
  MIN(usage_date) AS first_activity_date,
  MAX(usage_date) AS last_activity_date
FROM system.billing.usage
WHERE identity_metadata.run_as IS NOT NULL
  AND identity_metadata.run_as != ''
  AND usage_quantity > 0
  AND {INTERACTIVE_USAGE}
  AND usage_date >= DATE_SUB(CURRENT_DATE(), {LOOKBACK_DAYS})
GROUP BY identity_metadata.run_as
""")

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW badge_counts AS
SELECT user_id, COUNT(*) AS badge_count
FROM {tbl('badges')}
GROUP BY user_id
""")

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('user_profile_snapshot')} AS target
USING (
  SELECT
    p.user_id,
    SPLIT(p.user_id, '@')[0] AS display_name,
    CAST(p.total_points AS INT) AS total_points,
    CASE
      WHEN p.total_points >= 5000 THEN 'Elite'
      WHEN p.total_points >= 2000 THEN 'Platinum'
      WHEN p.total_points >= 800 THEN 'Gold'
      WHEN p.total_points >= 300 THEN 'Silver'
      ELSE 'Bronze'
    END AS level,
    COALESCE(s.current_streak, 0) AS current_streak,
    COALESCE(s.max_streak, 0) AS max_streak,
    COALESCE(b.badge_count, 0) AS badge_count,
    CAST(p.missions_completed AS INT) AS missions_completed,
    a.first_activity_date,
    a.last_activity_date,
    COALESCE(pr.distinct_products, 0) AS distinct_products_used,
    CAST('{NOW}' AS TIMESTAMP) AS updated_at
  FROM user_point_totals p
  LEFT JOIN (
    SELECT user_id,
           MAX(max_streak) AS max_streak,
           MAX(current_streak) AS current_streak
    FROM user_streaks
    GROUP BY user_id
  ) s ON p.user_id = s.user_id
  LEFT JOIN user_activity_dates a ON p.user_id = a.user_id
  LEFT JOIN badge_counts b ON p.user_id = b.user_id
  LEFT JOIN user_products pr ON p.user_id = pr.user_id
) AS source
ON target.user_id = source.user_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""")

print("User profile snapshots updated.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 7: Build Leaderboard

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('leaderboard')} AS target
USING (
  WITH mission_pts AS (
    SELECT user_id, SUM(points_awarded) AS pts,
      SUM(CASE WHEN completed_at >= DATE_SUB(CURRENT_DATE(), DAYOFWEEK(CURRENT_DATE()) % 7)
        THEN points_awarded ELSE 0 END) AS weekly_pts,
      SUM(CASE WHEN completed_at >= DATE_TRUNC('MONTH', CURRENT_DATE())
        THEN points_awarded ELSE 0 END) AS monthly_pts
    FROM {tbl('mission_completions')} GROUP BY user_id
  ),
  consumption_pts AS (
    SELECT user_id, SUM(points) AS pts,
      SUM(CASE WHEN event_timestamp >= DATE_SUB(CURRENT_DATE(), DAYOFWEEK(CURRENT_DATE()) % 7)
        THEN points ELSE 0 END) AS weekly_pts,
      SUM(CASE WHEN event_timestamp >= DATE_TRUNC('MONTH', CURRENT_DATE())
        THEN points ELSE 0 END) AS monthly_pts
    FROM {tbl('user_points_fact')} WHERE event_type = 'consumption' GROUP BY user_id
  ),
  totals AS (
    SELECT
      COALESCE(m.user_id, c.user_id) AS user_id,
      COALESCE(m.pts, 0) + COALESCE(c.pts, 0) AS total_points,
      COALESCE(m.weekly_pts, 0) + COALESCE(c.weekly_pts, 0) AS weekly_points,
      COALESCE(m.monthly_pts, 0) + COALESCE(c.monthly_pts, 0) AS monthly_points
    FROM mission_pts m FULL OUTER JOIN consumption_pts c ON m.user_id = c.user_id
  )
  SELECT
    t.user_id,
    SPLIT(t.user_id, '@')[0] AS display_name,
    t.total_points,
    t.weekly_points,
    t.monthly_points,
    CASE
      WHEN t.total_points >= 5000 THEN 'Elite'
      WHEN t.total_points >= 2000 THEN 'Platinum'
      WHEN t.total_points >= 800 THEN 'Gold'
      WHEN t.total_points >= 300 THEN 'Silver'
      ELSE 'Bronze'
    END AS level,
    CAST(ROW_NUMBER() OVER (ORDER BY t.total_points DESC) AS INT) AS all_time_rank,
    CAST(ROW_NUMBER() OVER (ORDER BY t.weekly_points DESC) AS INT) AS weekly_rank,
    CAST(ROW_NUMBER() OVER (ORDER BY t.monthly_points DESC) AS INT) AS monthly_rank,
    CAST('{NOW}' AS TIMESTAMP) AS updated_at
  FROM totals t
) AS source
ON target.user_id = source.user_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""")

print("Leaderboard updated.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 8: Award Badges

# COMMAND ----------

# Badge: Platform Explorer - 4+ distinct billing_origin_product values
spark.sql(f"""
MERGE INTO {tbl('badges')} AS target
USING (
  SELECT
    user_id,
    'platform_explorer' AS badge_id,
    'Platform Explorer' AS badge_name,
    'compass' AS badge_icon,
    CAST('{NOW}' AS TIMESTAMP) AS earned_at
  FROM user_products
  WHERE distinct_products >= 4
) AS source
ON target.user_id = source.user_id AND target.badge_id = source.badge_id
WHEN NOT MATCHED THEN INSERT *
""")

# Badge: Consistent Contributor - 14-day streak
spark.sql(f"""
MERGE INTO {tbl('badges')} AS target
USING (
  SELECT
    user_id,
    'consistent_contributor' AS badge_id,
    'Consistent Contributor' AS badge_name,
    'flame' AS badge_icon,
    CAST('{NOW}' AS TIMESTAMP) AS earned_at
  FROM (
    SELECT user_id, MAX(max_streak) AS best_streak
    FROM user_streaks
    GROUP BY user_id
  )
  WHERE best_streak >= 14
) AS source
ON target.user_id = source.user_id AND target.badge_id = source.badge_id
WHEN NOT MATCHED THEN INSERT *
""")

# Badge: Pipeline Craftsman - completed 5+ pipeline-related missions
spark.sql(f"""
MERGE INTO {tbl('badges')} AS target
USING (
  SELECT
    user_id,
    'pipeline_craftsman' AS badge_id,
    'Pipeline Craftsman' AS badge_name,
    'wrench' AS badge_icon,
    CAST('{NOW}' AS TIMESTAMP) AS earned_at
  FROM {tbl('mission_completions')}
  WHERE mission_id IN ('pipeline_builder', 'pipeline_runner', 'auto_loader_pioneer', 'consistent_operator', 'scheduler', 'job_creator')
  GROUP BY user_id
  HAVING COUNT(DISTINCT mission_id) >= 5
) AS source
ON target.user_id = source.user_id AND target.badge_id = source.badge_id
WHEN NOT MATCHED THEN INSERT *
""")

# Badge: AI Pioneer - completed 3+ of the AI/ML missions
# (matches BADGE_DEFINITIONS.ai_pioneer in app/main.py: required_missions=3,
# mission_filter = model_deployer / ai_function_builder / vector_search_pioneer /
# mlflow_experimenter). Previously defined in the UI but never awarded.
spark.sql(f"""
MERGE INTO {tbl('badges')} AS target
USING (
  SELECT
    user_id,
    'ai_pioneer' AS badge_id,
    'AI Pioneer' AS badge_name,
    'brain' AS badge_icon,
    CAST('{NOW}' AS TIMESTAMP) AS earned_at
  FROM {tbl('mission_completions')}
  WHERE mission_id IN ('model_deployer', 'ai_function_builder', 'vector_search_pioneer', 'mlflow_experimenter')
  GROUP BY user_id
  HAVING COUNT(DISTINCT mission_id) >= 3
) AS source
ON target.user_id = source.user_id AND target.badge_id = source.badge_id
WHEN NOT MATCHED THEN INSERT *
""")

# Badge: Full Stack - completed missions spanning 5+ distinct categories
# (matches BADGE_DEFINITIONS.full_stack: required_categories=5). Category is
# derived from mission_id here since mission_completions has no category column;
# the mapping mirrors MISSION_DEFINITIONS in app/main.py. Previously defined in
# the UI but never awarded.
spark.sql(f"""
MERGE INTO {tbl('badges')} AS target
USING (
  SELECT
    user_id,
    'full_stack' AS badge_id,
    'Full Stack' AS badge_name,
    'layers' AS badge_icon,
    CAST('{NOW}' AS TIMESTAMP) AS earned_at
  FROM (
    SELECT
      user_id,
      CASE
        WHEN mission_id IN ('first_steps', {", ".join(f"'{m}'" for m in GET_STARTED_MISSION_IDS)}) THEN 'Getting Started'
        WHEN mission_id IN ('job_creator','pipeline_builder','pipeline_runner','scheduler','auto_loader_pioneer','multi_task_orchestrator','liquid_clustering') THEN 'Data Engineering'
        WHEN mission_id IN ('genie_creator','genie_explorer','genie_curator','genie_power_user','dashboard_designer','dashboard_viewer','dashboard_publisher','dashboard_operator','data_explorer','power_analyst','query_author','alert_creator','app_builder','notebook_author','sql_analyst') THEN 'Analytics'
        WHEN mission_id IN ('genie_code_user','model_deployer','ai_function_builder','vector_search_pioneer','mlflow_experimenter','ml_practitioner') THEN 'AI / ML'
        WHEN mission_id IN ('lakebase_builder','lakebase_sync','lakebase_database','lakebase_connector') THEN 'Lakebase'
        WHEN mission_id = 'stream_starter' THEN 'Streaming'
        WHEN mission_id IN ('consistent_operator','daily_driver','cross_product_champion') THEN 'Engagement'
        WHEN mission_id = 'uc_publisher' THEN 'Governance'
        ELSE 'Other'
      END AS category
    FROM {tbl('mission_completions')}
  )
  GROUP BY user_id
  HAVING COUNT(DISTINCT category) >= 5
) AS source
ON target.user_id = source.user_id AND target.badge_id = source.badge_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Badges awarded.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 9: Generate Notifications for New Awards

# COMMAND ----------

# One notification per (user, mission, period) — NOT per run. Repeatable missions
# are deleted + re-inserted every run with a fresh scored_at, so the previous
# logic (created_at=NOW in the dedup key) produced a brand-new notification every
# 4 hours. Two changes fix it: (1) rebuild the table each run so already-
# accumulated duplicates are cleared, and (2) key each notification on the stable
# period identity (created_at = the completion's period_start, a DATE) so a
# mission already announced for a given week/month is never re-announced.
spark.sql(f"DELETE FROM {tbl('notifications')}")

spark.sql(f"""
MERGE INTO {tbl('notifications')} AS target
USING (
  SELECT
    user_id,
    'mission_complete' AS notification_type,
    CONCAT('Mission Complete: ', mission_name) AS title,
    CONCAT('You earned ', points_awarded, ' points for completing ', mission_name, '!') AS message,
    mission_id,
    points_awarded AS points,
    CAST(period_start AS TIMESTAMP) AS created_at
  FROM {tbl('mission_completions')}
) AS source
ON target.user_id = source.user_id
  AND target.mission_id = source.mission_id
  AND target.notification_type = source.notification_type
  AND target.created_at = source.created_at
WHEN NOT MATCHED THEN INSERT *
""")

# Badge notifications
spark.sql(f"""
MERGE INTO {tbl('notifications')} AS target
USING (
  SELECT
    user_id,
    'badge_earned' AS notification_type,
    CONCAT('Badge Unlocked: ', badge_name) AS title,
    CONCAT('Congratulations! You have earned the ', badge_name, ' badge!') AS message,
    badge_id AS mission_id,
    0 AS points,
    earned_at AS created_at
  FROM {tbl('badges')}
) AS source
ON target.user_id = source.user_id
  AND target.mission_id = source.mission_id
  AND target.notification_type = source.notification_type
WHEN NOT MATCHED THEN INSERT *
""")

print("Notifications generated.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 10: Update Badge Counts in Profiles

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('user_profile_snapshot')} AS target
USING (
  SELECT user_id, COUNT(*) AS badge_count
  FROM {tbl('badges')}
  GROUP BY user_id
) AS source
ON target.user_id = source.user_id
WHEN MATCHED AND target.badge_count != source.badge_count
  THEN UPDATE SET target.badge_count = source.badge_count, target.updated_at = CAST('{NOW}' AS TIMESTAMP)
""")

print("Badge counts synced to profiles.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 10b: Auto-Grant Permissions to App Service Principal
# MAGIC Looks up the Databricks App's service principal and grants it read access
# MAGIC to the Quest catalog and schema. This runs every time so new users don't
# MAGIC need to manually run SQL grants.

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import PermissionLevel, WarehouseAccessControlRequest, WarehousePermission

dbutils.widgets.text("warehouse_id", "", "SQL Warehouse ID")
WH_ID = dbutils.widgets.get("warehouse_id")

try:
    w = WorkspaceClient()
    app_info = w.apps.get(APP_NAME)

    # Try multiple attribute names — SDK versions vary
    sp_name = getattr(app_info, 'service_principal_name', None) \
        or getattr(app_info, 'effective_service_principal_name', None)
    sp_id = getattr(app_info, 'service_principal_id', None)
    sp_client_id = getattr(app_info, 'service_principal_client_id', None)

    # Fallback: look up SP display name by client ID
    if not sp_name and sp_client_id:
        try:
            for sp in w.service_principals.list(filter=f'applicationId eq "{sp_client_id}"'):
                sp_name = sp.display_name
                sp_id = sp.id
                break
        except Exception:
            pass

    if sp_name:
        print(f"App service principal: {sp_name} (ID: {sp_id})")

        # Grant catalog/schema permissions
        spark.sql(f"GRANT USE_CATALOG ON CATALOG `{CATALOG}` TO `{sp_name}`")
        spark.sql(f"GRANT USE_SCHEMA ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `{sp_name}`")
        spark.sql(f"GRANT SELECT ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `{sp_name}`")
        print(f"Granted catalog/schema access to {sp_name} on {CATALOG}.{SCHEMA}")

        # Grant CAN_USE on the SQL warehouse so the app can query data
        if WH_ID:
            try:
                w.warehouses.set_permissions(
                    warehouse_id=WH_ID,
                    access_control_list=[
                        WarehouseAccessControlRequest(
                            service_principal_name=sp_name,
                            permission_level=PermissionLevel.CAN_USE,
                        )
                    ],
                )
                print(f"Granted CAN_USE on warehouse {WH_ID} to {sp_name}")
            except Exception as wh_err:
                print(f"Warning: Warehouse grant failed (non-fatal): {wh_err}")
    else:
        print(f"Warning: Could not resolve service principal for app '{APP_NAME}'")
        print(f"  SP client ID: {sp_client_id}, SP ID: {sp_id}")
        print("  You may need to grant permissions manually. See SETUP.md troubleshooting.")
except Exception as e:
    print(f"Warning: Auto-grant failed (non-fatal): {e}")
    print("You may need to manually grant permissions. See SETUP.md troubleshooting.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Sweep stale service principals from output tables
# MAGIC MERGE updates/inserts but never deletes, so service-principal rows written by
# MAGIC earlier runs (before this filter existed) would linger in the profile,
# MAGIC leaderboard, badges and notifications. Remove them here. Human rows already
# MAGIC carry correct ranks (computed above over the cleaned fact tables).

# COMMAND ----------

for _t in ["user_profile_snapshot", "leaderboard", "badges", "notifications", "mission_completions", "user_points_fact"]:
    _n = spark.sql(f"SELECT COUNT(*) AS c FROM {tbl(_t)} WHERE {_human}").first()["c"]
    if _n:
        spark.sql(f"DELETE FROM {tbl(_t)} WHERE {_human}")
        print(f"  {_t}: removed {_n} stale service-principal rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------

total_users = spark.sql(f"SELECT COUNT(DISTINCT user_id) AS cnt FROM {tbl('user_profile_snapshot')}").first()["cnt"]
total_completions = spark.sql(f"SELECT COUNT(*) AS cnt FROM {tbl('mission_completions')}").first()["cnt"]
total_badges = spark.sql(f"SELECT COUNT(*) AS cnt FROM {tbl('badges')}").first()["cnt"]

print(f"""
Delta scoring complete.
  Users scored: {total_users}
  Mission completions: {total_completions}
  Badges awarded: {total_badges}
  Timestamp: {NOW}
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC Delta scoring complete. Lakebase sync is handled by the deploy script.

# COMMAND ----------

dbutils.notebook.exit(f"OK: {total_users} users, {total_completions} completions, {total_badges} badges")
