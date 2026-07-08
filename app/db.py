"""Centralized Lakebase (Postgres) connection handling for Databricks Quest.

This module is the single source of truth for connecting to Lakebase. Both the
FastAPI app and the migration runner use it so connection, auth, and
serialization behaviour stay consistent.

Connection strategy:
- In the running app, credentials come from the Databricks workspace identity
  (service principal OAuth token) via the Databricks SDK. Tokens are short-lived
  so the connection is cached for ~45 minutes and revalidated on reuse.
- Callers (e.g. the migration runner during deploy) may pass explicit
  ``user``/``password`` to bypass the SDK and connect with a pre-minted
  Lakebase credential.

The app must degrade gracefully when Lakebase is unavailable — callers are
expected to wrap query helpers in try/except and fall back to empty/default
responses, exactly as the adoption-mode endpoints already do.
"""

import os
import time
import logging
from contextlib import contextmanager
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("databricks-quest.db")

LAKEBASE_HOST = os.getenv("LAKEBASE_HOST", "")
LAKEBASE_DB = os.getenv("LAKEBASE_DB", "quest_db")
LAKEBASE_PORT = int(os.getenv("LAKEBASE_PORT", "5432"))

# Explicit writer credential (federation child role). When set, these override
# the Databricks workspace-OAuth path so a child app can authenticate to the
# MASTER workspace's shared Lakebase with the shared event-writer credential.
# This is the param-driven seam from ADR_006: standalone/master leave these
# unset and keep using workspace identity. ``LAKEBASE_WRITER_TOKEN`` is accepted
# as an alias for the password so the credential can be distributed as a token.
LAKEBASE_USER = os.getenv("LAKEBASE_USER", "").strip()
LAKEBASE_PASSWORD = (
    os.getenv("LAKEBASE_PASSWORD", "").strip()
    or os.getenv("LAKEBASE_WRITER_TOKEN", "").strip()
)

# Data backend: "lakebase" (Postgres) or "warehouse" (read scored Delta tables
# through a serverless SQL warehouse). Both are provisioned at deploy; admins
# flip the active one at runtime in Admin settings. QUEST_DATA_BACKEND is the
# deploy-time DEFAULT used until an admin overrides it (persisted in app_settings).
QUEST_DATA_BACKEND_DEFAULT = (os.getenv("QUEST_DATA_BACKEND", "lakebase").strip().lower() or "lakebase")
_VALID_BACKENDS = ("lakebase", "warehouse")

# Cache the active backend so we don't read the setting on every query.
_backend_cache: Dict[str, Any] = {"value": None, "expiry": 0}
_BACKEND_TTL_SECONDS = 30


def _lakebase_rows(query: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    """Read via Lakebase directly (never the warehouse) — used for app settings
    so the backend lookup itself can't recurse through routing."""
    import psycopg2.extras
    with _lease(autocommit=True) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()] if cur.description else []


def _ensure_app_settings() -> None:
    with _lease(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS app_settings "
                "(key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP)"
            )


# ── Backend-setting persistence ──────────────────────────────────────────────
# The admin's backend choice lives in TWO stores:
#   1. Delta `app_settings` in {QUEST_CATALOG}.{QUEST_SCHEMA}, read/written via
#      the SQL warehouse. AUTHORITATIVE whenever a warehouse is configured —
#      it stays reachable when Lakebase is down, which is exactly when an admin
#      needs to switch to the warehouse backend (the old Lakebase-only store
#      locked the escape hatch behind the broken door).
#   2. Lakebase `app_settings` (public schema). Authoritative only for
#      Lakebase-only deployments; otherwise a best-effort mirror.
# Writes when a warehouse is configured go Delta-first and MUST succeed there
# (a stale Delta row would override any Lakebase-only write on read).


def warehouse_ready() -> bool:
    """True when the warehouse-side store/backend is usable (warehouse + catalog set)."""
    return bool(
        os.getenv("QUEST_SQL_WAREHOUSE_ID", "").strip()
        and os.getenv("QUEST_CATALOG", "").strip()
    )


_DELTA_SETTINGS_DDL = (
    "CREATE TABLE IF NOT EXISTS app_settings "
    "(`key` STRING, value STRING, updated_at TIMESTAMP)"
)


def _delta_settings_read() -> Optional[str]:
    """Read the backend setting from the Delta store. None if absent/invalid."""
    import warehouse_backend as _wh
    rows = _wh.query("SELECT value FROM app_settings WHERE `key` = 'data_backend'")
    if rows:
        v = (rows[0].get("value") or "").strip().lower()
        if v in _VALID_BACKENDS:
            return v
    return None


def _delta_settings_write(value: str) -> None:
    import warehouse_backend as _wh
    try:
        _wh.query(_DELTA_SETTINGS_DDL)
    except Exception:
        pass  # SP may lack CREATE TABLE; deploy.sh pre-creates the table
    _wh.query(
        "MERGE INTO app_settings t USING (SELECT %s AS k, %s AS v) s ON t.`key` = s.k "
        "WHEN MATCHED THEN UPDATE SET value = s.v, updated_at = current_timestamp() "
        "WHEN NOT MATCHED THEN INSERT (`key`, value, updated_at) "
        "VALUES (s.k, s.v, current_timestamp())",
        ("data_backend", value),
    )


def _lakebase_settings_write(value: str) -> None:
    try:
        _ensure_app_settings()
    except Exception:
        pass  # table is pre-created by deploy.sh; the INSERT below is the real test
    with _lease(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_settings (key, value, updated_at) "
                "VALUES ('data_backend', %s, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (value,),
            )


def get_data_backend() -> str:
    """Active data backend, honoring the admin override (cached), else the deploy
    default. Falls back to the default if no setting can be read."""
    now = time.time()
    if _backend_cache["value"] and now < _backend_cache["expiry"]:
        return _backend_cache["value"]
    value: Optional[str] = None
    if warehouse_ready():
        try:
            value = _delta_settings_read()
        except Exception:
            value = None  # missing table / warehouse hiccup → fall back to Lakebase
    if value is None:
        try:
            rows = _lakebase_rows("SELECT value FROM app_settings WHERE key = 'data_backend'")
            if rows and (rows[0].get("value") or "").strip().lower() in _VALID_BACKENDS:
                value = rows[0]["value"].strip().lower()
        except Exception:
            pass  # no settings table / Lakebase unavailable
    if value is None:
        value = QUEST_DATA_BACKEND_DEFAULT
    _backend_cache.update(value=value, expiry=now + _BACKEND_TTL_SECONDS)
    return value


def set_data_backend(value: str) -> str:
    """Persist the admin's backend choice and refresh the cache. Admin-gated by the caller.

    Must work when Lakebase is down/read-only: with a warehouse configured the
    Delta store is written first (and is required to succeed); the Lakebase row
    is then mirrored best-effort so a later Lakebase-only readback agrees.
    """
    value = (value or "").strip().lower()
    if value not in _VALID_BACKENDS:
        raise ValueError(f"backend must be one of {_VALID_BACKENDS}")
    if warehouse_ready():
        try:
            _delta_settings_write(value)
        except Exception as exc:
            raise RuntimeError(f"could not write setting via SQL warehouse: {exc}") from exc
        try:
            _lakebase_settings_write(value)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Lakebase settings mirror failed (non-fatal): %s", exc)
    else:
        try:
            _lakebase_settings_write(value)
        except Exception as exc:
            raise RuntimeError(f"could not write setting to Lakebase: {exc}") from exc
    _backend_cache.update(value=value, expiry=time.time() + _BACKEND_TTL_SECONDS)
    return value


def warehouse_backend() -> bool:
    """True when adoption data is currently served from the SQL warehouse."""
    return get_data_backend() == "warehouse"

# OAuth tokens are valid ~1h; refresh the cached connection well before expiry.
_CONN_TTL_SECONDS = 2700  # 45 minutes
_conn_cache: Dict[str, Any] = {"conn": None, "expiry": 0}

# Bounded connection pool for the federation writer-credential path only.
# ADR_006 flags that many `child` apps each fanning connections into the MASTER
# workspace's shared Lakebase can exhaust connections; a small per-process pool
# caps and reuses them. The workspace-identity path (standalone/master/adoption)
# keeps using the single cached connection below and never touches this pool.
_POOL_MIN = int(os.getenv("LAKEBASE_POOL_MIN", "1"))
_POOL_MAX = int(os.getenv("LAKEBASE_POOL_MAX", "8"))
_pool: Any = None


def _writer_credential_path() -> bool:
    """True when an explicit writer credential is configured (federation child)."""
    return bool(LAKEBASE_USER and LAKEBASE_PASSWORD)


def _get_pool():
    """Lazily build the bounded ThreadedConnectionPool for the writer path."""
    global _pool
    if _pool is None:
        if not LAKEBASE_HOST:
            raise RuntimeError("LAKEBASE_HOST not configured")
        from psycopg2.pool import ThreadedConnectionPool

        _pool = ThreadedConnectionPool(
            _POOL_MIN,
            _POOL_MAX,
            host=LAKEBASE_HOST,
            port=LAKEBASE_PORT,
            dbname=LAKEBASE_DB,
            user=LAKEBASE_USER,
            password=LAKEBASE_PASSWORD,
            sslmode="require",
            connect_timeout=10,
        )
        logger.info(
            "Lakebase writer-credential pool created as %s (min=%d max=%d)",
            LAKEBASE_USER, _POOL_MIN, _POOL_MAX,
        )
    return _pool


def get_workspace_client():
    """Return a Databricks SDK WorkspaceClient.

    Imported lazily so this module can be imported in environments where the
    SDK is unavailable (e.g. a bare migration run with explicit credentials).
    """
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def serialize(obj: Any) -> Any:
    """Convert Postgres/Python types into JSON-serializable values."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def _workspace_credentials() -> Tuple[str, str]:
    """Resolve (user, password) from the Databricks workspace identity."""
    w = get_workspace_client()
    headers = w.config.authenticate()
    auth_header = headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else w.config.token
    user = w.current_user.me().user_name
    return user, token


def _connect(
    host: str,
    dbname: str,
    user: str,
    password: str,
    port: int,
    connect_timeout: int = 10,
):
    import psycopg2

    conn = psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        sslmode="require",
        connect_timeout=connect_timeout,
    )
    conn.autocommit = True
    return conn


def get_connection(
    host: Optional[str] = None,
    dbname: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    use_cache: bool = True,
):
    """Return a live psycopg2 connection to Lakebase.

    When ``user``/``password`` are supplied the connection is opened directly
    with those credentials and is NOT cached (used by the migration runner with
    a pre-minted credential). Otherwise credentials are resolved from the
    Databricks workspace identity and the connection is cached and revalidated.
    """
    host = host or LAKEBASE_HOST
    dbname = dbname or LAKEBASE_DB
    port = port or LAKEBASE_PORT

    if not host:
        raise RuntimeError("LAKEBASE_HOST not configured")

    # Explicit-credential path (no caching, no SDK).
    if user and password:
        return _connect(host, dbname, user, password, port)

    now = time.time()
    cached = _conn_cache["conn"]
    if use_cache and cached and _conn_cache["expiry"] > now:
        try:
            with cached.cursor() as c:
                c.execute("SELECT 1")
            return cached
        except Exception:
            try:
                cached.close()
            except Exception:
                pass

    # Resolve credentials. A configured explicit writer credential
    # (LAKEBASE_USER + LAKEBASE_PASSWORD/LAKEBASE_WRITER_TOKEN) takes precedence
    # over the workspace-OAuth path — this is how a federation `child` app
    # authenticates to the master's shared Lakebase. Standalone/master leave
    # these unset and fall through to the workspace identity, unchanged.
    if LAKEBASE_USER and LAKEBASE_PASSWORD:
        resolved_user, token = LAKEBASE_USER, LAKEBASE_PASSWORD
        auth_kind = "writer credential"
    else:
        resolved_user, token = _workspace_credentials()
        auth_kind = "workspace identity"
    conn = _connect(host, dbname, resolved_user, token, port)
    if use_cache:
        _conn_cache["conn"] = conn
        _conn_cache["expiry"] = now + _CONN_TTL_SECONDS
    logger.info("Lakebase connection established as %s (%s)", resolved_user, auth_kind)
    return conn


# Backwards-compatible alias: adoption-mode code historically called this name.
def get_lakebase_connection():
    return get_connection()


@contextmanager
def _lease(autocommit: bool):
    """Lease a connection for one unit of work, releasing it afterwards.

    Two paths, chosen by deployment shape:

    - Writer-credential (federation ``child`` → master): borrow from the bounded
      pool and return it afterwards (closing it if the work raised, so a poisoned
      session is never reused). This caps the connection fan-out into the shared
      master Lakebase per ADR_006.
    - Workspace-identity (standalone / master / adoption): reuse the single
      cached, revalidated connection exactly as before — unchanged behaviour.

    Commits/rolls back automatically when ``autocommit`` is False.
    """
    if _writer_credential_path():
        pool = _get_pool()
        conn = pool.getconn()
        broken = False
        try:
            conn.autocommit = autocommit
            yield conn
            if not autocommit:
                conn.commit()
        except Exception:
            broken = True
            if not autocommit:
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
            raise
        finally:
            try:
                pool.putconn(conn, close=broken)
            except Exception:  # noqa: BLE001
                pass
    else:
        conn = get_connection()
        prev = conn.autocommit
        conn.autocommit = autocommit
        try:
            yield conn
            if not autocommit:
                conn.commit()
        except Exception:
            if not autocommit:
                conn.rollback()
            raise
        finally:
            conn.autocommit = prev


def execute_query(query: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    """Run a read query and return a list of JSON-serializable dict rows."""
    if warehouse_backend():
        import warehouse_backend as _wh
        return _wh.query(query, params)

    import psycopg2.extras

    with _lease(autocommit=True) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            if cur.description:
                rows = cur.fetchall()
                return [{k: serialize(v) for k, v in dict(row).items()} for row in rows]
            return []


def execute(query: str, params: Tuple = ()) -> int:
    """Run a write statement and return the affected row count.

    Mutation callers that need multi-statement atomicity should use
    :func:`transaction` instead.
    """
    if warehouse_backend():
        # Warehouse backend is read-only (adoption mode). Writes (e.g. admin
        # allowlist) are not supported; admins come from QUEST_ADMIN_ALLOWLIST.
        raise RuntimeError("write not supported on warehouse data backend (read-only)")

    with _lease(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.rowcount


@contextmanager
def transaction():
    """Yield a cursor inside an atomic transaction.

    Commits on success, rolls back on any exception. Use for multi-statement
    writes that must be all-or-nothing (e.g. importing a quest pack).
    """
    with _lease(autocommit=False) as conn:
        with conn.cursor() as cur:
            yield cur


def healthcheck() -> bool:
    """Return True if a trivial query succeeds against Lakebase."""
    try:
        return len(execute_query("SELECT 1 AS ok")) > 0
    except Exception:
        return False


def applied_migrations() -> List[str]:
    """Return the list of applied migration versions, oldest first.

    Returns an empty list if Lakebase is unreachable or the
    ``schema_migrations`` table does not yet exist, so callers (e.g.
    ``/api/health``) never fail because migrations have not run.
    """
    try:
        rows = execute_query(
            "SELECT version FROM schema_migrations ORDER BY version ASC"
        )
        return [r["version"] for r in rows]
    except Exception:
        return []
