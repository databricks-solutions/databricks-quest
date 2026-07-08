# Databricks notebook source
# MAGIC %md
# MAGIC # Quest — Warm the SQL Warehouse (warehouse data backend)
# MAGIC
# MAGIC When the app serves adoption data from a serverless SQL warehouse
# MAGIC (`quest_data_backend=warehouse`), this task runs after each scoring cycle
# MAGIC to **start/warm the warehouse** so it's running right after the 4-hourly
# MAGIC refresh (and so the schedule keeps generating consumption). It runs a
# MAGIC trivial `SELECT 1`, which auto-starts the warehouse if it's stopped.
# MAGIC
# MAGIC No-ops for the Lakebase backend.

# COMMAND ----------

dbutils.widgets.text("quest_data_backend", "lakebase")
dbutils.widgets.text("warehouse_id", "")

backend = dbutils.widgets.get("quest_data_backend").strip().lower()
warehouse_id = dbutils.widgets.get("warehouse_id").strip()

if backend != "warehouse":
    dbutils.notebook.exit("SKIPPED: data backend is not 'warehouse'")
if not warehouse_id:
    dbutils.notebook.exit("SKIPPED: no warehouse_id provided")

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

# Best-effort explicit start (no-op if already running), then a trivial query
# that guarantees the warehouse is RUNNING and warm for the app.
try:
    w.warehouses.start(warehouse_id).result(timeout=__import__("datetime").timedelta(minutes=10))
except Exception as exc:
    print(f"start() note: {exc}")

resp = w.statement_execution.execute_statement(
    warehouse_id=warehouse_id, statement="SELECT 1", wait_timeout="30s"
)
state = resp.status.state.value if resp.status and resp.status.state else "?"
print(f"Warehouse {warehouse_id} warmed via SELECT 1 — statement state: {state}")
dbutils.notebook.exit(f"WARMED: {warehouse_id} ({state})")
