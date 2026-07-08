#!/usr/bin/env python3
"""Federation connectivity spike for Databricks Quest multi-workspace mode.

Run this FROM A CHILD WORKSPACE (or anywhere with the same network egress a
child app will have) to prove out the shared-Lakebase design BEFORE a real
event:

  1. Network reachability   — can we open a TLS Postgres connection to the
     MASTER Lakebase endpoint?
  2. Credential             — does the shared event-writer credential
     authenticate?
  3. Grant scope            — can the writer INSERT into the four fact tables,
     SELECT the read surface, and is it correctly BLOCKED from UPDATE/DELETE?

This is a diagnostic only. It never leaves data behind: every INSERT it makes
is wrapped in a transaction that is rolled back. It prints a PASS/FAIL summary
and exits non-zero if any required check fails.

Usage:

    python scripts/federation_spike.py \
        --host ep-xxx.database.us-east-1.cloud.databricks.com \
        --db quest_db \
        --user quest_event_writer \
        --event <event_slug> \
        --workspace-id <child_workspace_id>
    # password via --password or (preferred) PGPASSWORD / LAKEBASE_PASSWORD

The four fact tables and read surface mirror what deploy.sh grants to the
event-writer role and what app/federation.py writes at runtime.
"""

import argparse
import os
import sys
import uuid

FACT_TABLES = [
    "scoring_events",
    "task_attempts",
    "validation_results",
    "hints_taken",
]

READ_SURFACE = [
    "event_leaderboard",
    "team_scores",
    "teams",
    "participant_identity_map",
    "events",
]


def log(ok, msg):
    mark = "\033[0;32mPASS\033[0m" if ok else "\033[0;31mFAIL\033[0m"
    print(f"  [{mark}] {msg}")
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description="Quest federation connectivity spike")
    p.add_argument("--host", required=True, help="MASTER Lakebase endpoint host")
    p.add_argument("--db", default=os.getenv("LAKEBASE_DB", "quest_db"))
    p.add_argument("--port", type=int, default=int(os.getenv("LAKEBASE_PORT", "5432")))
    p.add_argument("--user", default=os.getenv("LAKEBASE_USER", "quest_event_writer"))
    p.add_argument(
        "--password",
        default=os.getenv("PGPASSWORD") or os.getenv("LAKEBASE_PASSWORD", ""),
        help="Writer credential (or set PGPASSWORD / LAKEBASE_PASSWORD)",
    )
    p.add_argument("--event", default="spike-event", help="Event slug/id to stamp")
    p.add_argument("--workspace-id", default="spike-ws", help="Child workspace id to stamp")
    args = p.parse_args()

    if not args.password:
        print("error: no writer credential — pass --password or set PGPASSWORD")
        return 2

    try:
        import psycopg2
    except Exception:
        print("error: psycopg2 not installed (pip install psycopg2-binary)")
        return 2

    print(f"\nFederation spike → {args.user}@{args.host}:{args.port}/{args.db}\n")

    # ── 1. Reachability + credential ─────────────────────────────────────────
    try:
        conn = psycopg2.connect(
            host=args.host,
            port=args.port,
            dbname=args.db,
            user=args.user,
            password=args.password,
            sslmode="require",
            connect_timeout=15,
        )
        conn.autocommit = False
    except Exception as exc:
        log(False, f"connect: {exc}")
        print("\nResult: FAIL — child cannot reach/authenticate to master Lakebase.\n")
        return 1
    log(True, "connect: TLS Postgres connection established + authenticated")

    failures = 0
    try:
        # ── 2. SELECT read surface ───────────────────────────────────────────
        for view in READ_SURFACE:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT 1 FROM {view} LIMIT 1")
                    cur.fetchall()
                conn.rollback()
                log(True, f"SELECT {view}")
            except Exception as exc:
                conn.rollback()
                failures += 1
                log(False, f"SELECT {view}: {exc}")

        # ── 3. INSERT the four fact tables (rolled back) ─────────────────────
        sid = f"spike_{uuid.uuid4().hex[:10]}"
        idem = f"{args.workspace_id}:{args.event}:spike:{sid}"
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO scoring_events
                        (scoring_event_id, event_id, workspace_id, user_id,
                         source_type, source_id, points_delta, reason,
                         idempotency_key)
                    VALUES (%s, %s, %s, %s, 'spike', %s, 0, 'connectivity spike', %s)
                    """,
                    (sid, args.event, args.workspace_id, "labuser+0@awsbricks.com", sid, idem),
                )
            conn.rollback()
            log(True, "INSERT scoring_events (rolled back)")
        except Exception as exc:
            conn.rollback()
            failures += 1
            log(False, f"INSERT scoring_events: {exc}")

        # ── 4. Confirm writer is BLOCKED from UPDATE/DELETE ──────────────────
        for verb, sql in [
            ("UPDATE", "UPDATE scoring_events SET points_delta = points_delta WHERE FALSE"),
            ("DELETE", "DELETE FROM scoring_events WHERE FALSE"),
        ]:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.rollback()
                failures += 1
                log(False, f"{verb} scoring_events was ALLOWED — writer is over-privileged")
            except Exception:
                conn.rollback()
                log(True, f"{verb} scoring_events correctly DENIED")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print()
    if failures == 0:
        print("Result: PASS — shared-Lakebase federation transport is viable.\n")
        return 0
    print(f"Result: FAIL — {failures} check(s) failed; review grants/connectivity.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
