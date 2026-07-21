#!/usr/bin/env python3
"""Seed synthetic rows into <catalog>.<schema>.training_completions for a Quest
demo of the Get Started training missions.

All users are FICTIONAL personas — never seed real names into a demo.

Covers the interesting cases so the missions + Learner bonus visibly light up:
  - jordan.hayes  : 3 distinct courses (DE, ML, GenAI)  -> 3 course missions + Learner
  - taylor.brooks : 2 distinct courses (SQL/BI, Gov)     -> 2 course missions + Learner
  - sam.okoro     : DE + DE-Japanese (SAME course, 2 rows) -> 1 course mission, NO Learner
  - riley.chen    : 1 course (Lakebase)                  -> 1 course mission, NO Learner
  - alex.morgan   : self-paced DE only (elearning)       -> NOTHING (self-paced never scores)

Usage:
  export DATABRICKS_HOST=... DATABRICKS_TOKEN=... DATABRICKS_AUTH_TYPE=pat
  python3 scripts/seed_training_completions.py --warehouse-id <id> --catalog <cat> --schema quest
"""
import argparse
import json
import time
import urllib.request

ROWS = [
    # (user_id, course_id, course_name, course_type, completed_at)
    ("jordan.hayes@example.com", "1511", "Get Started with Databricks for Data Engineering", "classroom", "2026-05-02 10:00:00"),
    ("jordan.hayes@example.com", "1512", "Get Started with Databricks for Machine Learning", "classroom", "2026-05-20 10:00:00"),
    ("jordan.hayes@example.com", "3514", "Get Started with Databricks for Generative AI", "classroom", "2026-06-11 10:00:00"),
    ("taylor.brooks@example.com", "1513", "Get Started with SQL Analytics and BI on Databricks", "classroom", "2026-04-15 09:30:00"),
    ("taylor.brooks@example.com", "4653", "Get Started with Data Governance on Databricks", "classroom", "2026-06-01 09:30:00"),
    # sam completed the SAME course twice (English + Japanese edition) -> 1 distinct
    ("sam.okoro@example.com", "1511", "Get Started with Databricks for Data Engineering", "classroom", "2026-03-10 14:00:00"),
    ("sam.okoro@example.com", "2438", "Get Started with Databricks for Data Engineering - Japanese", "classroom", "2026-03-25 14:00:00"),
    ("riley.chen@example.com", "5082", "Get Started with Lakebase", "classroom", "2026-06-18 11:00:00"),
    # alex only did a self-paced course -> must NOT score
    ("alex.morgan@example.com", "2469", "Get Started with Databricks for Data Engineering", "elearning", "2026-06-20 16:00:00"),
]


def run_sql(host, token, warehouse_id, statement):
    body = json.dumps({
        "warehouse_id": warehouse_id,
        "statement": statement,
        "wait_timeout": "50s",
    }).encode()
    req = urllib.request.Request(
        f"{host}/api/2.0/sql/statements/",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    # poll if still running
    sid = out.get("statement_id")
    while out.get("status", {}).get("state") in ("PENDING", "RUNNING"):
        time.sleep(2)
        preq = urllib.request.Request(
            f"{host}/api/2.0/sql/statements/{sid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(preq, timeout=60) as r:
            out = json.load(r)
    state = out.get("status", {}).get("state")
    if state != "SUCCEEDED":
        raise RuntimeError(f"SQL failed ({state}): {json.dumps(out.get('status', {}))}")
    return out


def main():
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse-id", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--schema", default="quest")
    args = ap.parse_args()

    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    token = os.environ["DATABRICKS_TOKEN"]
    fq = f"`{args.catalog}`.`{args.schema}`.training_completions"

    run_sql(host, token, args.warehouse_id, f"""
        CREATE TABLE IF NOT EXISTS {fq} (
          user_id STRING, course_id STRING, course_name STRING,
          course_type STRING, completed_at TIMESTAMP
        ) USING DELTA
    """)
    # idempotent reseed of just these demo users
    demo_users = sorted({r[0] for r in ROWS})
    in_list = ", ".join(f"'{u}'" for u in demo_users)
    run_sql(host, token, args.warehouse_id, f"DELETE FROM {fq} WHERE user_id IN ({in_list})")

    values = ", ".join(
        f"('{u}', '{cid}', '{name.replace(chr(39), chr(39)*2)}', '{ctype}', TIMESTAMP '{dt}')"
        for (u, cid, name, ctype, dt) in ROWS
    )
    run_sql(host, token, args.warehouse_id, f"""
        INSERT INTO {fq} (user_id, course_id, course_name, course_type, completed_at)
        VALUES {values}
    """)
    print(f"Seeded {len(ROWS)} rows for {len(demo_users)} demo users into {fq}")
    print("Expected after scoring:")
    print("  jordan.hayes  -> gs_data_engineering, gs_machine_learning, gs_generative_ai, databricks_learner  (250*3 + 500 = 1250)")
    print("  taylor.brooks -> gs_sql_analytics_bi, gs_data_governance, databricks_learner                     (250*2 + 500 = 1000)")
    print("  sam.okoro     -> gs_data_engineering only (English+Japanese = 1 course)                          (250)")
    print("  riley.chen    -> gs_lakebase only                                                                (250)")
    print("  alex.morgan   -> NOTHING (self-paced row does not score)                                         (0)")


if __name__ == "__main__":
    main()
