# Databricks notebook source
# MAGIC %md
# MAGIC # Quest — Attestation Round-trip (Lakebase → Delta), pre-scoring
# MAGIC
# MAGIC FIRST task of the scoring job. Self-attested Get Started course completions
# MAGIC (the tick-box) are written by the app into the durable Lakebase table
# MAGIC `training_attestations`. This task rolls any new ticks into the Delta
# MAGIC `training_completions` feed as `self_attested` rows so that the NEXT task
# MAGIC (`run_scoring`, scoring_pipeline.py Step 2b) reconciles them into points in
# MAGIC the SAME cycle.
# MAGIC
# MAGIC Ordering matters: this MUST run before `run_scoring` reads the feed. It used
# MAGIC to live at the top of `lakebase_sync.py` (which runs AFTER scoring), so a tick
# MAGIC only reached Delta one cycle too late — the same-cycle Delta→Lakebase overwrite
# MAGIC then wiped the app's instant-write serving rows, and points vanished for up to
# MAGIC a full cycle. Running the round-trip first closes that gap: scoring re-derives
# MAGIC the tick's rows the same cycle, and the sync overwrites Lakebase with identical
# MAGIC scored rows — no visible gap.
# MAGIC
# MAGIC Fail-loud by design: if this task errors, `run_scoring` and `sync_to_lakebase`
# MAGIC are skipped (UPSTREAM_FAILED), so the sync's DELETE+reinsert never runs and the
# MAGIC app's instant-write rows survive. The tick stays durable in `training_attestations`
# MAGIC and is retried next cycle. Serverless-safe: reads Lakebase via `psycopg2`, writes
# MAGIC Delta via Spark, mints a Lakebase credential at runtime.

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

# No Lakebase configured → the app is reading Delta directly (adoption/warehouse
# mode) and there are no attestations to round-trip. No-op so the downstream scoring
# task still runs normally.
if not LB_HOST:
    dbutils.notebook.exit("SKIPPED: no lakebase_host configured (no attestations to round-trip)")
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

import psycopg2

cred = lakebase_credential()
conn = psycopg2.connect(
    host=LB_HOST, port=5432, dbname=LB_DB, user=PG_USER, password=cred, sslmode="require"
)
conn.autocommit = False

# Roll self-attested course ticks Lakebase -> Delta so scoring can reconcile them
# THIS cycle. Idempotent on (user_id, course_id): re-merging an existing tick is a
# no-op, so a tick is never double-counted. NOT wrapped in a swallow-all try/except
# — a failure here should fail the task loudly (see fail-loud note above), keeping
# the app's instant-write serving rows intact until the next successful cycle.
try:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, course_id, course_mission_id, attested_at "
            "FROM training_attestations WHERE user_id IS NOT NULL AND course_id IS NOT NULL "
            "AND course_id <> ''"
        )
        attest_rows = cur.fetchall()
    conn.rollback()  # read-only; release the txn snapshot

    if not attest_rows:
        print("Attestation round-trip: no ticks to sync.")
    else:
        feed_tbl = f"`{CATALOG}`.`{SCHEMA}`.`training_completions`"
        spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {feed_tbl} (
          user_id STRING, course_id STRING, course_name STRING,
          course_type STRING, completed_at TIMESTAMP
        ) USING DELTA
        """)
        from pyspark.sql import Row
        # SELECT order is (user_id, course_id, course_mission_id, attested_at). The
        # feed's course_name column is a display-only field scoring never reads (it
        # keys on course_id + course_type), so we label the self_attested rows with
        # the mission id — enough to identify them in the feed without a lookup.
        src = spark.createDataFrame([
            Row(user_id=r[0], course_id=str(r[1]), course_name=r[2],
                course_type="self_attested", completed_at=r[3])
            for r in attest_rows
        ])
        src.createOrReplaceTempView("_attest_src")
        spark.sql(f"""
        MERGE INTO {feed_tbl} AS t
        USING _attest_src AS s
        ON t.user_id = s.user_id AND t.course_id = s.course_id AND t.course_type = 'self_attested'
        WHEN NOT MATCHED THEN INSERT (user_id, course_id, course_name, course_type, completed_at)
          VALUES (s.user_id, s.course_id, s.course_name, s.course_type, s.completed_at)
        """)
        print(f"Attestation round-trip: merged {len(attest_rows)} tick(s) into Delta training_completions.")
finally:
    conn.close()
