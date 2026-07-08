# Databricks notebook source
# MAGIC %md
# MAGIC # Quest — Delta → Lakebase Sync
# MAGIC
# MAGIC Second task of the scoring job. The first task (`scoring_pipeline.py`)
# MAGIC rescored the Unity Catalog Delta tables; this task copies them into the
# MAGIC Lakebase Postgres DB the app reads from, so the app refreshes every cycle
# MAGIC (not just at deploy time).
# MAGIC
# MAGIC Serverless-safe: reads Delta via Spark, writes Lakebase via `psycopg2`,
# MAGIC and mints a Lakebase credential at runtime via `POST /api/2.0/postgres/credentials`.

# COMMAND ----------

dbutils.widgets.text("quest_catalog", "")
dbutils.widgets.text("quest_schema", "quest")
dbutils.widgets.text("lakebase_host", "")
dbutils.widgets.text("lakebase_db", "quest_db")
dbutils.widgets.text("app_name", "databricks-quest")

CATALOG = dbutils.widgets.get("quest_catalog").strip()
SCHEMA = dbutils.widgets.get("quest_schema").strip() or "quest"
LB_HOST = dbutils.widgets.get("lakebase_host").strip()
LB_DB = dbutils.widgets.get("lakebase_db").strip() or "quest_db"
APP_NAME = dbutils.widgets.get("app_name").strip() or "databricks-quest"

if not LB_HOST:
    dbutils.notebook.exit("SKIPPED: no lakebase_host configured (adoption app may be reading Delta directly)")
if not CATALOG:
    raise ValueError("quest_catalog is required")

# COMMAND ----------

import json
import urllib.request

ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
HOST = ctx.apiUrl().get()
TOKEN = ctx.apiToken().get()
# Run-as identity (Lakebase Postgres role name). Users connect as their email.
PG_USER = ctx.userName().get()
LB_PROJECT = "".join(c if (c.isalnum() or c == "-") else "-" for c in APP_NAME.lower())


def lakebase_credential():
    """Mint a short-lived Lakebase Postgres credential for the run-as identity."""
    body = json.dumps(
        {"endpoint": f"projects/{LB_PROJECT}/branches/production/endpoints/primary"}
    ).encode()
    req = urllib.request.Request(
        f"{HOST}/api/2.0/postgres/credentials",
        data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["token"]


# COMMAND ----------

# (table, ordered columns) — must match the Delta tables scoring_pipeline writes
# and the Lakebase tables deploy.sh provisions. Replace-in-place each cycle.
TABLES = [
    ("mission_completions", ["user_id", "mission_id", "mission_name", "points_awarded", "completed_at", "period_start", "period_end", "scored_at"]),
    ("user_points_fact", ["user_id", "event_type", "mission_id", "points", "reason", "event_timestamp", "scored_at"]),
    ("user_profile_snapshot", ["user_id", "display_name", "total_points", "level", "current_streak", "max_streak", "badge_count", "missions_completed", "first_activity_date", "last_activity_date", "distinct_products_used", "updated_at"]),
    ("leaderboard", ["user_id", "display_name", "total_points", "weekly_points", "monthly_points", "level", "all_time_rank", "weekly_rank", "monthly_rank", "updated_at"]),
    ("badges", ["user_id", "badge_id", "badge_name", "badge_icon", "earned_at"]),
    ("notifications", ["user_id", "notification_type", "title", "message", "mission_id", "points", "created_at"]),
]

# COMMAND ----------

import psycopg2
from psycopg2.extras import execute_values

cred = lakebase_credential()
conn = psycopg2.connect(
    host=LB_HOST, port=5432, dbname=LB_DB, user=PG_USER, password=cred, sslmode="require"
)
conn.autocommit = False

synced, skipped = 0, []
try:
    for table, cols in TABLES:
        fqn = f"`{CATALOG}`.`{SCHEMA}`.`{table}`"
        try:
            rows = spark.table(f"{CATALOG}.{SCHEMA}.{table}").select(*cols).collect()
        except Exception as exc:  # table not produced this cycle — skip, don't fail the job
            skipped.append(f"{table} (read: {str(exc)[:80]})")
            continue
        data = [tuple(r[c] for c in cols) for r in rows]
        with conn.cursor() as cur:
            # Replace-in-place: each table is a full snapshot of the scored state.
            cur.execute(f"DELETE FROM {table}")
            if data:
                collist = ", ".join(cols)
                execute_values(cur, f"INSERT INTO {table} ({collist}) VALUES %s", data, page_size=500)
        conn.commit()
        print(f"  {table}: {len(data)} rows synced")
        synced += 1
    print(f"Lakebase sync complete: {synced}/{len(TABLES)} tables; skipped={skipped}")
finally:
    conn.close()
