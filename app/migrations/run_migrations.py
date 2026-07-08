#!/usr/bin/env python3
"""Idempotent Lakebase migration runner for Databricks Quest GameDay.

Applies every ``*.sql`` file in this directory (ordered by filename) that has
not yet been recorded in the ``schema_migrations`` table, then records it. Safe
to run repeatedly — already-applied migrations are skipped and the SQL itself is
written to be idempotent (``CREATE ... IF NOT EXISTS`` / ``CREATE OR REPLACE``).

Usage:

    # Explicit credentials (used by deploy.sh with a pre-minted Lakebase token):
    PGPASSWORD=$TOKEN python app/migrations/run_migrations.py \
        --lakebase-host ep-xxx.database.cloud.databricks.com \
        --lakebase-db quest_db \
        --user me@example.com

    # Inside Databricks (resolve credentials from the workspace identity):
    python app/migrations/run_migrations.py --lakebase-host ... --lakebase-db ...

Connection backends, in order of preference:
- psycopg2 with explicit ``--user`` + ``PGPASSWORD``/``--password``.
- psql CLI fallback (no psycopg2 needed locally) when explicit creds are given.
- psycopg2 via Databricks SDK OAuth when no password is supplied (app context).
"""

import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Set, Tuple

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(THIS_DIR)

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version     TEXT PRIMARY KEY,
  description TEXT,
  applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def log(msg: str) -> None:
    print(f"[migrations] {msg}", flush=True)


def discover_migrations(migrations_dir: str) -> List[Tuple[str, str, str]]:
    """Return ordered (version, description, path) for each .sql migration."""
    out: List[Tuple[str, str, str]] = []
    for path in sorted(glob.glob(os.path.join(migrations_dir, "*.sql"))):
        version = Path(path).stem  # e.g. "001_gameday_core"
        with open(path, "r", encoding="utf-8") as fh:
            sql = fh.read()
        m = re.search(r"--\s*description:\s*(.+)", sql, re.IGNORECASE)
        description = m.group(1).strip() if m else version
        out.append((version, description, path))
    return out


# ── psycopg2 backend ─────────────────────────────────────────────────────────


class Psycopg2Backend:
    """Apply migrations transactionally using a psycopg2 connection."""

    def __init__(self, conn):
        self.conn = conn

    def ensure_table(self) -> None:
        prev = self.conn.autocommit
        self.conn.autocommit = True
        try:
            with self.conn.cursor() as cur:
                cur.execute(SCHEMA_MIGRATIONS_DDL)
        finally:
            self.conn.autocommit = prev

    def applied_versions(self) -> Set[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations")
            return {row[0] for row in cur.fetchall()}

    def apply(self, version: str, description: str, path: str) -> None:
        with open(path, "r", encoding="utf-8") as fh:
            sql = fh.read()
        self.conn.autocommit = False
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version, description) "
                    "VALUES (%s, %s) ON CONFLICT (version) DO NOTHING",
                    (version, description),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self.conn.autocommit = True

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


# ── psql CLI backend (no local psycopg2 needed) ──────────────────────────────


class PsqlBackend:
    """Apply migrations by shelling out to the psql CLI."""

    def __init__(self, host: str, dbname: str, user: str, password: str, port: int):
        self.conninfo = (
            f"host={host} port={port} dbname={dbname} user={user} sslmode=require"
        )
        self.env = os.environ.copy()
        self.env["PGPASSWORD"] = password

    def _psql(self, args: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["psql", self.conninfo, *args],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=120,
        )

    def ensure_table(self) -> None:
        res = self._psql(["-v", "ON_ERROR_STOP=1", "-c", SCHEMA_MIGRATIONS_DDL])
        if res.returncode != 0:
            raise RuntimeError(f"ensure schema_migrations failed: {res.stderr.strip()}")

    def applied_versions(self) -> Set[str]:
        res = self._psql(["-t", "-A", "-c", "SELECT version FROM schema_migrations"])
        if res.returncode != 0:
            raise RuntimeError(f"read schema_migrations failed: {res.stderr.strip()}")
        return {line.strip() for line in res.stdout.splitlines() if line.strip()}

    def apply(self, version: str, description: str, path: str) -> None:
        # Apply the migration body atomically.
        res = self._psql(["--single-transaction", "-v", "ON_ERROR_STOP=1", "-f", path])
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or res.stdout.strip())
        # Record it (idempotent insert).
        safe_desc = description.replace("'", "''")
        rec = self._psql(
            [
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "INSERT INTO schema_migrations (version, description) VALUES "
                f"('{version}', '{safe_desc}') ON CONFLICT (version) DO NOTHING",
            ]
        )
        if rec.returncode != 0:
            raise RuntimeError(f"record migration failed: {rec.stderr.strip()}")

    def close(self) -> None:
        pass


def _have_psycopg2() -> bool:
    try:
        import psycopg2  # noqa: F401

        return True
    except Exception:
        return False


def build_backend(args):
    """Construct the most appropriate backend from args/env."""
    host = args.lakebase_host or os.getenv("LAKEBASE_HOST", "")
    dbname = args.lakebase_db or os.getenv("LAKEBASE_DB", "quest_db")
    port = args.port or int(os.getenv("LAKEBASE_PORT", "5432"))
    user = args.user or os.getenv("LAKEBASE_USER") or os.getenv("PGUSER")
    password = args.password or os.getenv("PGPASSWORD")

    if not host:
        raise SystemExit("error: --lakebase-host (or LAKEBASE_HOST) is required")

    # Explicit credentials: prefer psycopg2, fall back to psql CLI.
    if user and password:
        if _have_psycopg2():
            import psycopg2

            conn = psycopg2.connect(
                host=host,
                port=port,
                dbname=dbname,
                user=user,
                password=password,
                sslmode="require",
                connect_timeout=15,
            )
            conn.autocommit = True
            log(f"connected via psycopg2 as {user}")
            return Psycopg2Backend(conn)
        log("psycopg2 unavailable; using psql CLI backend")
        return PsqlBackend(host, dbname, user, password, port)

    # No password: resolve credentials from the Databricks workspace identity.
    log("no explicit password; resolving Databricks workspace credentials")
    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    import db as appdb  # type: ignore

    conn = appdb.get_connection(host=host, dbname=dbname, port=port, use_cache=False)
    log("connected via Databricks SDK OAuth")
    return Psycopg2Backend(conn)


def run(args) -> int:
    migrations_dir = args.migrations_dir or THIS_DIR
    migrations = discover_migrations(migrations_dir)
    if not migrations:
        log(f"no migration files found in {migrations_dir}")
        return 0

    backend = build_backend(args)
    try:
        backend.ensure_table()
        applied = backend.applied_versions()
        log(f"{len(applied)} migration(s) already applied")

        newly_applied = 0
        for version, description, path in migrations:
            if version in applied:
                log(f"skip   {version} (already applied)")
                continue
            log(f"apply  {version} — {description}")
            backend.apply(version, description, path)
            newly_applied += 1

        log(
            f"done: {newly_applied} applied, "
            f"{len(migrations) - newly_applied} skipped, "
            f"{len(migrations)} total"
        )
        return 0
    finally:
        backend.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Lakebase GameDay migrations")
    parser.add_argument("--lakebase-host", help="Lakebase endpoint host")
    parser.add_argument("--lakebase-db", help="Lakebase database name")
    parser.add_argument("--port", type=int, help="Postgres port (default 5432)")
    parser.add_argument("--user", help="Postgres user (e.g. your workspace email)")
    parser.add_argument(
        "--password",
        help="Postgres password / Lakebase token (or set PGPASSWORD)",
    )
    parser.add_argument(
        "--migrations-dir",
        help="Directory of .sql migrations (default: alongside this script)",
    )
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001 - surface a clean error to deploy.sh
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
