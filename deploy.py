#!/usr/bin/env python3
"""Databricks Quest -- cross-platform Python deployer.

A Windows/macOS/Linux installer that reproduces ``deploy.sh``'s full flow --
including Lakebase -- with no bash, no Postgres command-line client, and no
Terraform.
It drives the Databricks REST API through the Databricks SDK (WorkspaceClient)
and talks to Lakebase Postgres through ``psycopg2`` (imported lazily so the
warehouse backend never needs it).

Usage:
    python deploy.py --catalog quest_data
    python deploy.py --catalog quest_data --data-backend warehouse --skip-scoring
    python deploy.py --profile my-profile --catalog quest_data --warehouse "My WH"

Requirements:
    - Databricks CLI authenticated (``databricks auth login --host ...``) or
      DATABRICKS_HOST/DATABRICKS_TOKEN in the environment.
    - Python 3.9+ with databricks-sdk and PyYAML. psycopg2-binary is needed only
      for ``--data-backend lakebase``; it is a wheel, so no PostgreSQL install.
    - Any databricks-sdk release works: the Lakebase API group moved from
      ``w.database_instances`` to ``w.database`` in 0.56 and both are handled.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

# ── Colors (ANSI, only when attached to a real terminal) ──────────────────────
_TTY = sys.stdout.isatty()
RED = "\033[0;31m" if _TTY else ""
GREEN = "\033[0;32m" if _TTY else ""
YELLOW = "\033[1;33m" if _TTY else ""
BLUE = "\033[0;34m" if _TTY else ""
CYAN = "\033[0;36m" if _TTY else ""
BOLD = "\033[1m" if _TTY else ""
NC = "\033[0m" if _TTY else ""


def _pick_glyphs() -> dict:
    """Choose status markers stdout can actually encode.

    Windows falls back to the locale codepage (cp1252/cp437) whenever stdout is
    redirected to a file or pipe, and printing "✓" there raises
    UnicodeEncodeError. Probe the real encoding once and drop to ASCII when the
    Unicode markers would not survive.
    """
    fancy = {"ok": "\u2713", "warn": "\u26a0", "err": "\u2717", "info": "\u2192"}
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "".join(fancy.values()).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return {"ok": "[OK]", "warn": "[!]", "err": "[X]", "info": "->"}
    return fancy


GLYPH = _pick_glyphs()


# ── Logging helpers (staged output, mirrors install_lakemeter.py) ─────────────
# flush=True throughout: stdout is block-buffered when piped or redirected, and a
# five-minute deploy that prints nothing until it exits looks hung in CI logs.
def log_step(step: int, total: int, msg: str) -> None:
    print(f"\n{YELLOW}[{step}/{total}]{NC} {BOLD}{msg}{NC}", flush=True)


def log_ok(msg: str) -> None:
    print(f"  {GREEN}{GLYPH['ok']}{NC} {msg}", flush=True)


def log_warn(msg: str) -> None:
    print(f"  {YELLOW}{GLYPH['warn']}{NC} {msg}", flush=True)


def log_err(msg: str) -> None:
    print(f"  {RED}{GLYPH['err']}{NC} {msg}", flush=True)


def log_info(msg: str) -> None:
    print(f"  {BLUE}{GLYPH['info']}{NC} {msg}", flush=True)


# ── Resolved options ──────────────────────────────────────────────────────────
@dataclass
class Config:
    """Resolved deployment options (parsed args + derived identity)."""

    profile: Optional[str] = None
    host: Optional[str] = None
    warehouse: Optional[str] = None
    warehouse_id: Optional[str] = None
    catalog: str = ""
    schema: str = "quest"
    app_name: str = "databricks-quest"
    data_backend: str = "lakebase"
    admins: str = ""
    lakebase_db: str = "quest_db"
    lakebase_host: str = ""
    skip_scoring: bool = False
    non_interactive: bool = False


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (all flags from the Global Constraints)."""
    p = argparse.ArgumentParser(
        prog="deploy.py",
        description="Deploy Databricks Quest (cross-platform: pure Python, SDK-driven).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--profile", default=None, help="Databricks CLI profile to use.")
    p.add_argument(
        "--host",
        default=None,
        help="Workspace URL (used when no profile/env auth is configured).",
    )
    p.add_argument(
        "--catalog",
        default="",
        help=(
            "Unity Catalog for Quest's scored tables. Omit it and you are asked "
            "to pick an existing catalog or name a new one (required with "
            "--non-interactive)."
        ),
    )
    p.add_argument("--schema", default="quest", help="Schema name for Quest tables.")
    p.add_argument(
        "--app-name", default="databricks-quest", help="Databricks App name."
    )
    p.add_argument(
        "--warehouse", default=None, help="SQL Warehouse name (matched case-insensitively)."
    )
    p.add_argument(
        "--warehouse-id", default=None, help="SQL Warehouse ID (skips warehouse lookup)."
    )
    p.add_argument(
        "--data-backend",
        choices=["lakebase", "warehouse"],
        default="lakebase",
        help="Deploy-time default data backend for the app.",
    )
    p.add_argument(
        "--admins",
        default="",
        help="Comma-separated emails seeded as Admin-page admins.",
    )
    p.add_argument(
        "--lakebase-host",
        default="",
        help="Existing Lakebase endpoint host (skips provisioning when set).",
    )
    p.add_argument(
        "--lakebase-db", default="quest_db", help="Lakebase database name."
    )
    p.add_argument(
        "--skip-scoring",
        action="store_true",
        help="Skip running the scoring pipeline + scheduled job.",
    )
    p.add_argument(
        "--non-interactive",
        "-y",
        action="store_true",
        help="Never prompt; pick sensible defaults (for CI / customers).",
    )
    return p


def _config_from_args(args: argparse.Namespace) -> Config:
    """Fold parsed args into a :class:`Config` (identity filled in by ``main``)."""
    return Config(
        profile=args.profile,
        host=args.host,
        warehouse=args.warehouse,
        warehouse_id=args.warehouse_id,
        catalog=args.catalog,
        schema=args.schema,
        app_name=args.app_name,
        data_backend=args.data_backend,
        admins=args.admins,
        lakebase_db=args.lakebase_db,
        lakebase_host=args.lakebase_host,
        skip_scoring=args.skip_scoring,
        non_interactive=args.non_interactive,
    )


# ── Pure helpers: sanitization + app.yaml rendering ───────────────────────────
def sanitize_project_id(app_name: str) -> str:
    """App name -> Lakebase project id.

    Matches deploy.sh:1168 exactly: lowercase, then replace every character
    that is not ``[a-z0-9-]`` with ``-``.
    """
    return re.sub(r"[^a-z0-9-]", "-", app_name.lower())


def render_app_yaml(
    *,
    backend: str,
    catalog: str,
    schema: str,
    warehouse_id: str,
    admins: str,
    lakebase_host: str = "",
    lakebase_db: str = "",
) -> str:
    """Render ``app/app.yaml`` for the given backend.

    A SQL warehouse and the catalog/schema are ALWAYS emitted (the runtime
    backend toggle persists to a Delta ``app_settings`` table via the warehouse,
    so it must keep working even when Lakebase is down -- see deploy.sh
    ``write_app_yaml``). ``QUEST_ADMIN_ALLOWLIST`` is emitted only when admins is
    non-empty. ``LAKEBASE_HOST``/``LAKEBASE_DB`` are emitted ONLY for the
    ``lakebase`` backend and only when a host is known -- warehouse-backend
    deploys must never carry Lakebase env.
    """
    env: List[dict] = []

    # Lakebase env: lakebase backend only, and only when a host is known.
    if backend == "lakebase" and lakebase_host:
        env.append({"name": "LAKEBASE_HOST", "value": lakebase_host})
        env.append({"name": "LAKEBASE_DB", "value": lakebase_db})

    # Deploy-time default backend + catalog/schema (always, for the runtime toggle).
    env.append({"name": "QUEST_DATA_BACKEND", "value": backend})
    if catalog:
        env.append({"name": "QUEST_CATALOG", "value": catalog})
        env.append({"name": "QUEST_SCHEMA", "value": schema})
    if warehouse_id:
        env.append({"name": "QUEST_SQL_WAREHOUSE_ID", "value": warehouse_id})
    if admins:
        env.append({"name": "QUEST_ADMIN_ALLOWLIST", "value": admins})

    doc = {
        "command": ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
        "env": env,
    }
    # sort_keys=False keeps command before env; default_flow_style=False -> block style.
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


# ── SQL / DDL builders (pure) ─────────────────────────────────────────────────
def uc_schema_statements(catalog: str, schema: str) -> List[str]:
    """Schema plus the Delta ``app_settings`` table, assuming the catalog exists.

    ``app_settings`` is pre-created as the deploying user so the app service
    principal never needs CREATE TABLE for the runtime backend toggle.
    """
    return [
        f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}",
        (
            f"CREATE TABLE IF NOT EXISTS {catalog}.{schema}.app_settings "
            "(`key` STRING, value STRING, updated_at TIMESTAMP)"
        ),
    ]


def can_prompt(non_interactive: bool) -> bool:
    """True when there is a human at a terminal to answer a question.

    Piping the output to a file, or running from CI, leaves stdin closed, and
    ``input()`` there aborts the deploy with a bare "EOF when reading a line".
    """
    return not non_interactive and sys.stdin is not None and sys.stdin.isatty()


def is_missing_catalog_error(exc: Exception) -> bool:
    """True when a statement failed only because the catalog does not exist."""
    return "NO_SUCH_CATALOG" in str(exc).upper()


#: Catalogs that exist everywhere and are never a sensible target for Quest.
_BUILTIN_CATALOGS = {"system", "samples", "hive_metastore", "__databricks_internal"}


def suggest_catalogs(w, limit: int = 8) -> List[str]:
    """Names of catalogs the deploying user could plausibly use for Quest.

    Someone new to Databricks who hits "cannot create catalog" has no idea what
    to pass instead, so offer the ones they can already see.
    """
    try:
        names = [c.name for c in w.catalogs.list() if c.name]
    except Exception:  # noqa: BLE001
        return []
    return [n for n in names if n not in _BUILTIN_CATALOGS][:limit]


DEFAULT_NEW_CATALOG = "quest_data"


def resolve_catalog(w, catalog: Optional[str], non_interactive: bool) -> str:
    """Decide which Unity Catalog to use, asking when nothing was passed.

    Someone new to Databricks does not know what to put in ``--catalog``, so when
    it is omitted list the catalogs they can already see and let them pick one or
    name a new one. Anyone who passed ``--catalog`` is taken at their word, and a
    non-interactive run without it still fails fast rather than guessing.
    """
    if catalog:
        return catalog

    if not can_prompt(non_interactive):
        raise RuntimeError(
            "--catalog is required (the Unity Catalog for Quest's scored tables).\n"
            "    Re-run with --catalog NAME, or run it interactively to be asked."
        )

    options = suggest_catalogs(w)
    print("\n  Quest stores its scored tables in a Unity Catalog catalog.")
    if options:
        print("  Use an existing catalog, or create a new one:\n")
        for i, name in enumerate(options, 1):
            print(f"    {i}) {name}")
        print(f"    n) create a new catalog")
        choice = input("\n  Choose [1]: ").strip() or "1"
        if choice.lower() not in ("n", "new"):
            try:
                idx = int(choice) - 1
            except ValueError:
                idx = -1
            if not 0 <= idx < len(options):
                raise RuntimeError(f"Invalid catalog selection: {choice!r}")
            log_ok(f"Using existing catalog '{options[idx]}'")
            return options[idx]
    else:
        print("  You have no catalogs yet, so Quest will create one.")

    name = input(f"  New catalog name [{DEFAULT_NEW_CATALOG}]: ").strip()
    name = name or DEFAULT_NEW_CATALOG
    log_info(f"Will create catalog '{name}' (needs CREATE CATALOG on the metastore).")
    return name


def setup_unity_catalog(
    w, warehouse_id: str, catalog: str, schema: str, confirm_missing: bool = False
) -> None:
    """Create the Quest schema, creating the catalog first only if it is missing.

    Schema-first mirrors deploy.sh. Most deploying identities have CREATE SCHEMA
    on an existing catalog but not metastore-level CREATE CATALOG, and some
    metastores (accounts on Default Storage) reject a bare ``CREATE CATALOG``
    without an explicit MANAGED LOCATION. Attempting the catalog unconditionally
    turns those perfectly valid setups into a hard failure.
    """
    try:
        run_uc_statements(w, warehouse_id, uc_schema_statements(catalog, schema))
        return
    except RuntimeError as exc:
        if not is_missing_catalog_error(exc):
            raise

    # Only worth asking when the name came from --catalog: creating a catalog is
    # a metastore-level change and a typo should not silently make a second one.
    # Someone who just picked "create a new catalog" has already answered this.
    if confirm_missing:
        answer = input(
            f"\n  Catalog '{catalog}' does not exist. Create it? [Y/n]: "
        ).strip().lower()
        if answer in ("n", "no"):
            raise RuntimeError(
                f"Stopped: catalog '{catalog}' does not exist and was not created.\n"
                f"    Re-run with --catalog pointing at an existing catalog."
            )

    log_info(f"Catalog '{catalog}' not found -- creating it...")
    try:
        run_uc_statements(w, warehouse_id, [f"CREATE CATALOG IF NOT EXISTS {catalog}"])
    except RuntimeError as exc:
        options = suggest_catalogs(w)
        hint = (
            f"    Catalogs you already have access to: {', '.join(options)}\n"
            f"    Re-run with one of them, for example:\n"
            f"      python deploy.py --catalog {options[0]}\n"
            if options
            else "    Ask a metastore admin to create it and grant you CREATE SCHEMA.\n"
        )
        raise RuntimeError(
            f"Could not create catalog '{catalog}'.\n"
            f"    {exc}\n"
            f"{hint}"
            f"    Creating one yourself in the UI also works (the UI handles\n"
            f"    Default Storage accounts correctly)."
        ) from exc

    run_uc_statements(w, warehouse_id, uc_schema_statements(catalog, schema))


def uc_grant_statements(catalog: str, schema: str, sp: str) -> List[str]:
    """Grant the app service principal Unity Catalog access.

    MODIFY is required (not just SELECT) because the app upserts the
    backend-toggle row into the Delta ``app_settings`` table -- see deploy.sh:290.
    The SP client id is quoted with backticks; the catalog/schema identifiers are
    plain (Quest's names are valid unquoted identifiers).
    """
    return [
        f"GRANT USE CATALOG ON CATALOG {catalog} TO `{sp}`",
        f"GRANT USE SCHEMA, SELECT, MODIFY ON SCHEMA {catalog}.{schema} TO `{sp}`",
    ]


def lakebase_ddl() -> str:
    """The Lakebase schema the app and the scoring job both expect.

    Six scored tables (mission_completions, user_points_fact,
    user_profile_snapshot, leaderboard, badges, notifications) plus
    ``app_settings`` for the runtime backend toggle and ``training_attestations``
    for self-attested course ticks. The last one is not optional: the
    ``roundtrip_attestations`` job task reads it on every run and the whole job
    fails without it. Idempotent via IF NOT EXISTS so it is safe to re-run.
    """
    return """
CREATE TABLE IF NOT EXISTS mission_completions (
  user_id TEXT, mission_id TEXT, mission_name TEXT, points_awarded INT,
  completed_at TIMESTAMP, period_start DATE, period_end DATE, scored_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_points_fact (
  user_id TEXT, event_type TEXT, mission_id TEXT, points INT,
  reason TEXT, event_timestamp TIMESTAMP, scored_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_profile_snapshot (
  user_id TEXT, display_name TEXT, total_points INT, level TEXT,
  current_streak INT, max_streak INT, badge_count INT, missions_completed INT,
  first_activity_date DATE, last_activity_date DATE, distinct_products_used INT,
  updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS leaderboard (
  user_id TEXT, display_name TEXT, total_points INT, weekly_points INT,
  monthly_points INT, level TEXT, all_time_rank INT, weekly_rank INT,
  monthly_rank INT, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS badges (
  user_id TEXT, badge_id TEXT, badge_name TEXT, badge_icon TEXT, earned_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notifications (
  id SERIAL PRIMARY KEY, user_id TEXT, notification_type TEXT, title TEXT,
  message TEXT, mission_id TEXT, points INT, created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP
);
-- Self-attested Get Started course completions (the tick-box). Durable: the
-- scoring rebuild never truncates it, and roundtrip_attestations rolls it into
-- Delta each cycle. One row per user+course.
CREATE TABLE IF NOT EXISTS training_attestations (
  user_id TEXT, course_mission_id TEXT, course_id TEXT, attested_at TIMESTAMP,
  UNIQUE (user_id, course_mission_id)
);
CREATE INDEX IF NOT EXISTS idx_mc_user ON mission_completions(user_id);
CREATE INDEX IF NOT EXISTS idx_lb_rank ON leaderboard(all_time_rank);
CREATE INDEX IF NOT EXISTS idx_ups_user ON user_profile_snapshot(user_id);
CREATE INDEX IF NOT EXISTS idx_badges_user ON badges(user_id);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id);
""".strip()


#: Lakebase tables the app writes to at runtime, not just reads.
#: - app_settings: the Admin data-backend toggle upserts here.
#: - training_attestations: the Get Started tick-box records a course here.
#: - mission_completions, user_points_fact, user_profile_snapshot, leaderboard:
#:   the tick-box's instant award writes these in the SAME atomic transaction
#:   (_award_mission_tx + _bump_user_totals_tx). deploy.sh reaches them through
#:   DATABRICKS_SUPERUSER membership, which deploy.py drops on purpose (it fails
#:   silently on some workspaces), so they must be granted explicitly here or the
#:   whole attest transaction rolls back with a permission error (a 503 tick-box).
APP_WRITABLE_TABLES = (
    "app_settings",
    "training_attestations",
    "mission_completions",
    "user_points_fact",
    "user_profile_snapshot",
    "leaderboard",
)


def lakebase_grant_statements(sp: str) -> List[str]:
    """Postgres-side grants for the app service principal.

    Blanket read on existing tables plus a default privilege so tables created
    later are also readable, CREATE on ``public`` so the app can make its own
    tables, and explicit writes on the two tables the app actually writes:
    ``app_settings`` (the backend toggle) and ``training_attestations`` (course
    ticks). deploy.sh leaves the second one to DATABRICKS_SUPERUSER membership,
    which its own comments note has failed silently on customer workspaces, so
    grant it directly instead. Identifiers are double-quoted because the SP
    client id contains hyphens.
    """
    stmts = [
        f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{sp}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO "{sp}"',
        f'GRANT CREATE ON SCHEMA public TO "{sp}"',
    ]
    stmts += [
        f'GRANT SELECT, INSERT, UPDATE ON {table} TO "{sp}"'
        for table in APP_WRITABLE_TABLES
    ]
    return stmts


# ── Databricks client layer (all Databricks API calls go through the SDK) ─────
def get_client(profile: Optional[str] = None, host: Optional[str] = None):
    """Build a WorkspaceClient.

    Precedence: an explicit ``profile`` wins, then an explicit ``host``, then a
    bare client that picks up ambient auth (env vars / default profile). The SDK
    is imported lazily so importing this module never requires it.
    """
    from databricks.sdk import WorkspaceClient

    if profile:
        return WorkspaceClient(profile=profile)
    if host:
        return WorkspaceClient(host=host)
    return WorkspaceClient()


def _state_value(obj) -> Optional[str]:
    """Best-effort extraction of a state string from an SDK enum/response."""
    state = getattr(getattr(obj, "status", None), "state", None)
    if state is None:
        return None
    return getattr(state, "value", state)


def resolve_warehouse(
    w, name: Optional[str], wid: Optional[str], non_interactive: bool
) -> str:
    """Resolve a SQL warehouse id.

    An explicit ``wid`` wins (no lookup). Otherwise match ``name``
    case-insensitively against ``w.warehouses.list()``. With no name and no
    match, a user at a terminal is prompted; anyone else (``--non-interactive``,
    piped output, CI) gets the first existing warehouse, and if the workspace has
    none, a dedicated 2X-Small serverless PRO warehouse (60-min auto-stop) is
    created. Mirrors deploy.sh's "always wire a warehouse" behavior.
    """
    if wid:
        return wid

    warehouses = list(w.warehouses.list())

    if name:
        target = name.lower()
        for wh in warehouses:
            if (wh.name or "").lower() == target:
                return wh.id
        raise RuntimeError(f"No warehouse found matching name: {name!r}")

    if warehouses and can_prompt(non_interactive):
        print("\n  Available SQL Warehouses:")
        for i, wh in enumerate(warehouses, 1):
            print(f"    {i}) {wh.name} ({wh.id})")
        choice = input("  Select warehouse number [1]: ").strip() or "1"
        try:
            idx = int(choice) - 1
        except ValueError:
            idx = 0
        if 0 <= idx < len(warehouses):
            return warehouses[idx].id
        raise RuntimeError("Invalid warehouse selection.")

    if warehouses:
        reason = "Non-interactive" if non_interactive else "No interactive terminal"
        log_info(f"{reason} -- defaulting to warehouse '{warehouses[0].name}'.")
        log_info("Pass --warehouse NAME or --warehouse-id ID to choose another.")
        return warehouses[0].id

    # None exist: provision a dedicated 2X-Small serverless warehouse.
    from databricks.sdk.service.sql import CreateWarehouseRequestWarehouseType

    log_info("No warehouse found -- provisioning a 2X-Small serverless warehouse...")
    created = w.warehouses.create(
        name="databricks-quest-warehouse",
        cluster_size="2X-Small",
        auto_stop_mins=60,
        max_num_clusters=1,
        enable_serverless_compute=True,
        warehouse_type=CreateWarehouseRequestWarehouseType.PRO,
    )
    # create() returns a Wait[...]; prefer the resolved response id when present.
    result = getattr(created, "response", created)
    return result.id


def run_uc_statements(w, warehouse_id: str, statements: List[str]) -> None:
    """Execute a list of Unity Catalog SQL statements via the Statements API.

    No session catalog is set: every statement Quest issues is fully qualified,
    and pinning the session to a catalog that the first statement is about to
    create would fail before it ever ran. Each statement runs with
    ``wait_timeout="50s"`` (the API cap); a terminal failure state raises so the
    deploy stops instead of silently shipping a broken app.
    """
    _FAILED = ("FAILED", "CANCELED", "CLOSED")
    for stmt in statements:
        resp = w.statement_execution.execute_statement(
            statement=stmt,
            warehouse_id=warehouse_id,
            wait_timeout="50s",
        )
        state = _state_value(resp)
        # A concrete FAILED/CANCELED/CLOSED state is a real error. Unknown
        # (None / mocked) states are treated as non-fatal.
        if isinstance(state, str) and state in _FAILED:
            err = getattr(getattr(resp.status, "error", None), "message", None)
            raise RuntimeError(f"UC statement failed ({state}): {err or stmt}")


def upload_dir(w, local_dir: Path, workspace_dir: str) -> int:
    """Recursively upload ``local_dir`` into ``workspace_dir`` via the SDK.

    Pure-``pathlib`` walk (Windows-safe), base64 content, ImportFormat.AUTO.
    Skips ``__pycache__``/``.pyc``. Returns the number of files uploaded.
    """
    import base64
    from databricks.sdk.service.workspace import ImportFormat

    local_dir = Path(local_dir)
    if not local_dir.exists():
        raise RuntimeError(f"Source directory not found: {local_dir}")

    made = set()

    def _mkdirs(path: str) -> None:
        if path and path not in made:
            try:
                w.workspace.mkdirs(path)
            except Exception:
                pass
            made.add(path)

    _mkdirs(workspace_dir)
    uploaded = 0
    for item in sorted(local_dir.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(local_dir)
        rel_posix = rel.as_posix()
        if "__pycache__" in rel_posix or rel_posix.endswith(".pyc"):
            continue
        parent = rel.parent.as_posix()
        if parent and parent != ".":
            _mkdirs(f"{workspace_dir}/{parent}")
        target = f"{workspace_dir}/{rel_posix}"
        w.workspace.import_(
            path=target,
            content=base64.b64encode(item.read_bytes()).decode("ascii"),
            format=ImportFormat.AUTO,
            overwrite=True,
        )
        uploaded += 1
    return uploaded


def upload_text(w, text: str, workspace_path: str) -> None:
    """Upload a single generated file to the workspace."""
    import base64
    from databricks.sdk.service.workspace import ImportFormat

    w.workspace.import_(
        path=workspace_path,
        content=base64.b64encode(text.encode("utf-8")).decode("ascii"),
        format=ImportFormat.AUTO,
        overwrite=True,
    )


def create_or_get_app(w, name: str, description: str):
    """Create the Databricks App, tolerating an already-existing app.

    Returns the App object (from ``w.apps.get(name)``), which exposes ``.url``,
    ``.app_status.state``, and ``.service_principal_client_id``.
    """
    from databricks.sdk.service.apps import App

    try:
        created = w.apps.create(app=App(name=name, description=description))
        # create() returns Wait[App]; block for the resource when possible.
        result = getattr(created, "result", None)
        if callable(result):
            try:
                result()
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        if "already exists" not in str(exc).lower():
            log_warn(f"App create returned: {str(exc)[:160]}")
    return w.apps.get(name=name)


def deploy_app(w, name: str, source_path: str) -> None:
    """Deploy uploaded app source to the app and wait for it to go live.

    Uses ``apps.deploy_and_wait`` so the URL printed at the end points at a
    running deployment. A slow deploy should not fail the installer, so a
    waiter timeout is logged and swallowed (the deployment keeps running server
    side); other errors propagate to the caller.
    """
    from databricks.sdk.service.apps import AppDeployment

    try:
        w.apps.deploy_and_wait(
            app_name=name,
            app_deployment=AppDeployment(source_code_path=source_path),
        )
    except TimeoutError as exc:
        log_warn(f"App deploy still running (waiter timed out): {str(exc)[:140]}")


def grant_sp_warehouse(w, warehouse_id: str, sp: str) -> None:
    """Grant the app service principal CAN_USE on the SQL warehouse.

    The app runs as the SP, so the warehouse backend (and the runtime backend
    toggle, which can switch any deploy to warehouse mode) needs the SP to be
    able to use the warehouse -- mirrors deploy.sh ``grant_sp_warehouse_access``'s
    PATCH of ``/permissions/warehouses/{id}`` with CAN_USE.
    """
    from databricks.sdk.service.sql import (
        WarehouseAccessControlRequest,
        WarehousePermissionLevel,
    )

    w.warehouses.update_permissions(
        warehouse_id=warehouse_id,
        access_control_list=[
            WarehouseAccessControlRequest(
                service_principal_name=sp,
                permission_level=WarehousePermissionLevel.CAN_USE,
            )
        ],
    )


ENVIRONMENT_KEY = "default"


def job_name(app_name: str) -> str:
    """Scoring job name, scoped to the app.

    The app name is part of it so two Quest deployments in one workspace get two
    jobs. A fixed name would make the second deploy silently overwrite the first
    deployment's schedule, since the job is looked up by name.
    """
    return f"[Quest] Scoring Pipeline ({app_name})"


def scoring_task_graph(notebooks_dir: str, catalog: str, schema: str,
                       warehouse_id: str, app_name: str, data_backend: str,
                       lakebase_host: str, lakebase_db: str) -> List[dict]:
    """Describe the four scoring tasks and their dependencies.

    Mirrors the DAB job in ``databricks.yml``. Order matters: the attestation
    round-trip has to land in Delta before scoring reads the feed, and the
    Lakebase sync has to run after scoring so the app sees the fresh numbers.
    Returned as plain dicts so both the one-time submit and the scheduled job can
    build their own task type from one definition.
    """
    lakebase_params = {
        "quest_catalog": catalog,
        "quest_schema": schema,
        "lakebase_host": lakebase_host,
        "lakebase_db": lakebase_db,
        "app_name": app_name,
    }
    return [
        {
            "task_key": "roundtrip_attestations",
            "notebook_path": f"{notebooks_dir}/roundtrip_attestations",
            "params": lakebase_params,
            "depends_on": [],
        },
        {
            "task_key": "run_scoring",
            "notebook_path": f"{notebooks_dir}/scoring_pipeline",
            "params": {
                "quest_catalog": catalog,
                "quest_schema": schema,
                "app_name": app_name,
                "warehouse_id": warehouse_id,
            },
            "depends_on": ["roundtrip_attestations"],
        },
        {
            "task_key": "sync_to_lakebase",
            "notebook_path": f"{notebooks_dir}/lakebase_sync",
            "params": lakebase_params,
            "depends_on": ["run_scoring"],
        },
        {
            "task_key": "warm_warehouse",
            "notebook_path": f"{notebooks_dir}/warm_warehouse",
            "params": {
                "quest_data_backend": data_backend,
                "warehouse_id": warehouse_id,
            },
            "depends_on": ["run_scoring"],
        },
    ]


def _job_environments():
    """Serverless environment for the job.

    ``lakebase_sync`` and ``roundtrip_attestations`` import psycopg2, which is not
    preinstalled on serverless compute, so declare it the same way
    ``databricks.yml`` does.
    """
    from databricks.sdk.service.compute import Environment
    from databricks.sdk.service.jobs import JobEnvironment

    return [
        JobEnvironment(
            environment_key=ENVIRONMENT_KEY,
            spec=Environment(client="1", dependencies=["psycopg2-binary"]),
        )
    ]


def _is_bundle_managed(w, job_id: int) -> bool:
    """True when the job is owned by a Databricks Asset Bundle.

    deploy.sh deploys through DAB, and with the default app name the bundle's job
    has exactly the name this deployer looks for. Rewriting a bundle-owned job
    behind Terraform's back desynchronises bundle state, so detect it and leave
    it alone.
    """
    try:
        deployment = w.jobs.get(job_id=job_id).settings.deployment
    except Exception:  # noqa: BLE001
        return False
    kind = getattr(deployment, "kind", None)
    return str(getattr(kind, "value", kind) or "").upper() == "BUNDLE"


def _build_tasks(graph: List[dict], task_cls):
    """Turn the task graph into SDK ``Task`` objects."""
    from databricks.sdk.service.jobs import NotebookTask, TaskDependency

    tasks = []
    for spec in graph:
        tasks.append(
            task_cls(
                task_key=spec["task_key"],
                notebook_task=NotebookTask(
                    notebook_path=spec["notebook_path"],
                    base_parameters=spec["params"],
                ),
                depends_on=[TaskDependency(task_key=d) for d in spec["depends_on"]]
                or None,
                environment_key=ENVIRONMENT_KEY,
            )
        )
    return tasks


def trigger_scoring(w, job_id: int):
    """Kick off an immediate run of the scheduled job.

    Deliberately ``run_now`` on the job rather than a detached ``jobs.submit``:
    a one-off run would ignore the job's ``max_concurrent_runs=1`` and could
    overlap the 4-hourly schedule, which collides on a Delta
    ConcurrentDeleteReadException. Going through the job also keeps the manual
    run in the same history the schedule writes to.
    """
    return w.jobs.run_now(job_id=job_id)


def create_scheduled_job(w, graph: List[dict], app_name: str):
    """Create or update the every-4-hours scoring job.

    Looks the job up by name first and resets the existing one instead of
    creating a second copy, so re-running the deployer is safe. Cron
    ``0 0 */4 * * ?`` UTC, and ``max_concurrent_runs=1`` because the scoring
    notebook does whole-table DELETE + re-INSERT and overlapping runs collide on
    a Delta ConcurrentDeleteReadException.
    """
    from databricks.sdk.service.jobs import (
        CronSchedule,
        JobSettings,
        PauseStatus,
        QueueSettings,
        Task,
    )

    name = job_name(app_name)
    settings = JobSettings(
        name=name,
        tasks=_build_tasks(graph, Task),
        environments=_job_environments(),
        max_concurrent_runs=1,
        # With concurrency pinned to 1, queue an overlapping trigger instead of
        # rejecting it -- a deploy-time run must not fail because the 4-hourly
        # schedule happens to be mid-flight.
        queue=QueueSettings(enabled=True),
        schedule=CronSchedule(
            quartz_cron_expression="0 0 */4 * * ?",
            timezone_id="UTC",
            pause_status=PauseStatus.UNPAUSED,
        ),
    )

    existing = next((j for j in w.jobs.list(name=name)), None)
    if existing is not None:
        if _is_bundle_managed(w, existing.job_id):
            log_warn(
                f"Job '{name}' is managed by a Databricks Asset Bundle "
                "(deploy.sh); leaving it untouched."
            )
            log_info("Use deploy.sh for it, or pass a different --app-name.")
            return existing.job_id
        # update, not reset: reset replaces the whole settings object and would
        # silently drop tags, notifications, or timeouts someone added by hand.
        w.jobs.update(job_id=existing.job_id, new_settings=settings)
        return existing.job_id

    created = w.jobs.create(
        name=settings.name,
        tasks=settings.tasks,
        environments=settings.environments,
        max_concurrent_runs=settings.max_concurrent_runs,
        queue=settings.queue,
        schedule=settings.schedule,
    )
    return created.job_id


# ── Lakebase provisioning (SDK instance + lazy-psycopg2 DDL/grants) ───────────
LAKEBASE_CAPACITY = "CU_1"


def lakebase_api(w):
    """Return the SDK API group that owns Lakebase database instances.

    The Lakebase surface moved between SDK releases: ``w.database_instances``
    (added in databricks-sdk 0.54) was removed in 0.56 and replaced by
    ``w.database``. Prefer the current name and fall back to the old one so the
    deployer works on both an unpinned ``pip install databricks-sdk`` and the
    0.55 pin in ``app/requirements.txt``.
    """
    for attr in ("database", "database_instances"):
        api = getattr(w, attr, None)
        if api is not None:
            return api
    raise RuntimeError(
        "This databricks-sdk has no Lakebase API (neither w.database nor "
        "w.database_instances). Upgrade with: pip install -U databricks-sdk"
    )


def lakebase_types():
    """Return ``(DatabaseInstance, DatabaseInstanceState)`` for the running SDK.

    These dataclasses live in ``databricks.sdk.service.database`` on 0.56+ and
    in ``databricks.sdk.service.catalog`` on 0.54/0.55.
    """
    try:
        from databricks.sdk.service.database import (
            DatabaseInstance,
            DatabaseInstanceState,
        )
    except ImportError:
        from databricks.sdk.service.catalog import (  # type: ignore[no-redef]
            DatabaseInstance,
            DatabaseInstanceState,
        )
    return DatabaseInstance, DatabaseInstanceState


def _instance_state(inst) -> str:
    """Normalize a ``DatabaseInstance.state`` (enum or str) to its string value."""
    st = getattr(inst, "state", None)
    if st is None:
        return ""
    return str(getattr(st, "value", st))


def provision_lakebase(w, project_id: str) -> dict:
    """Get-or-create a Lakebase instance, wait until AVAILABLE, return its host.

    Returns ``{"host": <endpoint dns>, "instance_name": <name>}``. Creates the
    instance then polls ``get_database_instance`` until the state is AVAILABLE;
    the endpoint host is the ``read_write_dns`` attribute.
    """
    import time

    db = lakebase_api(w)
    DatabaseInstance, DatabaseInstanceState = lakebase_types()

    ready_state = DatabaseInstanceState.AVAILABLE.value  # "AVAILABLE"
    name = project_id
    host = None

    # 1. Reuse an existing instance if present. If it exists but isn't AVAILABLE
    #    yet (still STARTING/UPDATING, no read_write_dns), fall through to the poll
    #    loop -- do NOT create a duplicate.
    exists = False
    try:
        existing = db.get_database_instance(name=name)
        exists = True
        host = getattr(existing, "read_write_dns", None)
        if host and _instance_state(existing) == ready_state:
            log_ok(f"Lakebase instance '{name}' already exists ({host})")
            return {"host": host, "instance_name": name}
        log_info(f"Lakebase instance '{name}' exists but is still starting...")
    except Exception:
        exists = False  # not found -> create below

    # 2. Create it (only when it does not already exist). In 0.55 create returns
    #    the DatabaseInstance directly (no Wait wrapper).
    if not exists:
        log_info(f"Creating Lakebase instance '{name}' ({LAKEBASE_CAPACITY})...")
        created = db.create_database_instance(
            database_instance=DatabaseInstance(
                name=name, capacity=LAKEBASE_CAPACITY, stopped=False
            )
        )
        # 0.56+ wraps this in a Wait[...]; unwrap when present.
        inst = getattr(created, "response", created)
        host = getattr(inst, "read_write_dns", None) or host

    # 3. Poll until the instance is AVAILABLE with an endpoint host. Lakebase only
    #    exposes read_write_dns once the instance is usable (a fresh instance
    #    reports STARTING with no host), so we require both.
    for i in range(120):  # ~10 min max
        polled = db.get_database_instance(name=name)
        host = getattr(polled, "read_write_dns", None) or host
        state = _instance_state(polled)
        if state in ("FAILED", "DELETING"):
            raise RuntimeError(f"Lakebase instance entered {state} state")
        if state == ready_state and host:
            log_ok(f"Lakebase endpoint ready: {host}")
            return {"host": host, "instance_name": name}
        if i % 10 == 0:
            log_info(f"  waiting for Lakebase ({state or 'provisioning'})...")
        time.sleep(5)

    raise RuntimeError("Timed out waiting for the Lakebase endpoint to be ready")


def _lakebase_credential(w, instance_name: str) -> str:
    """Mint a short-lived Lakebase credential token.

    Prefers the SDK's ``generate_database_credential``. Older SDKs do not expose
    it, so fall back to the REST endpoint the scoring job's sync notebook already
    uses (``POST /api/2.0/postgres/credentials``), which accepts the instance name
    as a project endpoint path.
    """
    import uuid

    gen = getattr(lakebase_api(w), "generate_database_credential", None)
    if gen is not None:
        cred = gen(request_id=str(uuid.uuid4()), instance_names=[instance_name])
        return cred.token

    resp = w.api_client.do(
        "POST",
        "/api/2.0/postgres/credentials",
        body={
            "endpoint": (
                f"projects/{instance_name}/branches/production/endpoints/primary"
            )
        },
    )
    token = (resp or {}).get("token")
    if not token:
        raise RuntimeError(f"Lakebase credential request returned no token: {resp}")
    return token


def lakebase_connect(host: str, user: str, token: str, dbname: str):
    """Open a psycopg2 connection to Lakebase (psycopg2 imported lazily).

    Uses ``sslmode=require`` on port 5432, as the Lakebase endpoint requires TLS.
    Kept thin so callers can inject their own connection factory in tests.
    """
    import psycopg2

    return psycopg2.connect(
        host=host,
        port=5432,
        dbname=dbname,
        user=user,
        password=token,
        sslmode="require",
        connect_timeout=15,
    )


def lakebase_create_db_and_tables(conn_factory, db: str, ddl: str) -> None:
    """Create the Lakebase database (if missing) then the 6 Quest tables.

    ``conn_factory`` is a callable ``(dbname) -> connection`` so the caller
    controls credentials/host and tests can inject a fake. The database is
    created from a connection to the default ``postgres`` db; tables/indexes are
    then created inside ``db``.
    """
    # 1. Create the database (idempotent) from the 'postgres' maintenance db.
    admin_conn = conn_factory("postgres")
    try:
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
            if not cur.fetchone():
                cur.execute(f"CREATE DATABASE {db}")
    finally:
        admin_conn.close()

    # 2. Create tables + indexes inside the target database.
    conn = conn_factory(db)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(ddl)
    finally:
        conn.close()


def grant_sp_lakebase(w, project_id: str, sp: str, host: str, user: str,
                      dbname: str) -> None:
    """Grant the app service principal read access on the Lakebase schema.

    Registers the SP as a Lakebase role (SERVICE_PRINCIPAL identity) via the SDK
    database API, then applies the Postgres-side SELECT grants over psycopg2
    (lazy). Best-effort: role registration tolerates "already exists".
    """
    # Register the SP as a Lakebase role so OAuth login works. Older SDKs do not
    # expose the role types at all, in which case this is a best-effort no-op and
    # the Postgres GRANTs below still run.
    try:
        try:
            from databricks.sdk.service.database import (
                DatabaseInstanceRole,
                DatabaseInstanceRoleIdentityType,
            )
        except ImportError:
            from databricks.sdk.service.catalog import (  # type: ignore[no-redef]
                DatabaseInstanceRole,
                DatabaseInstanceRoleIdentityType,
            )

        lakebase_api(w).create_database_instance_role(
            instance_name=project_id,
            database_instance_role=DatabaseInstanceRole(
                name=sp,
                identity_type=DatabaseInstanceRoleIdentityType.SERVICE_PRINCIPAL,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        # Re-running a deploy hits an already-registered role. The API words that
        # as a conflict rather than "already exists", so match both and stay quiet.
        benign = ("already exists", "conflicts with existing")
        if not any(phrase in str(exc).lower() for phrase in benign):
            log_warn(f"Lakebase SP role registration: {str(exc)[:140]}")

    # Apply the Postgres SELECT grants as the deploying user.
    token = _lakebase_credential(w, project_id)
    conn = lakebase_connect(host, user, token, dbname)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for stmt in lakebase_grant_statements(sp):
                cur.execute(stmt)
    finally:
        conn.close()


TOTAL_STEPS = 10


def _resolve_admins(cfg: Config, user_email: str) -> str:
    """Default the admin allowlist to the deploying user (so the Admin page is
    never wide open), matching deploy.sh's behavior."""
    if cfg.admins:
        return cfg.admins
    if user_email and "@" in user_email:
        return user_email
    return ""


def _workspace_app_dir(user_email: str, app_name: str) -> str:
    """Workspace path the app source is uploaded to (mirrors deploy.sh quick mode)."""
    return f"/Workspace/Users/{user_email}/{app_name}/app"


def _notebooks_dir(user_email: str, app_name: str) -> str:
    return f"/Workspace/Users/{user_email}/{app_name}/notebooks"


def run(cfg: Config) -> int:
    """Execute the full 10-step deployment flow. Returns a process exit code."""
    repo_root = Path(__file__).resolve().parent
    app_dir = repo_root / "app"
    notebooks_dir = repo_root / "notebooks"

    # ── Step 1: prerequisites + client ────────────────────────────────────────
    log_step(1, TOTAL_STEPS, "Checking prerequisites and connecting")
    w = get_client(cfg.profile, cfg.host)

    # ── Step 2: identity ──────────────────────────────────────────────────────
    log_step(2, TOTAL_STEPS, "Identifying the deploying user")
    me = w.current_user.me()
    user_email = getattr(me, "user_name", "") or ""
    log_ok(f"Authenticated as {user_email}")
    cfg.admins = _resolve_admins(cfg, user_email)

    # ── Step 3: SQL warehouse (always wired, every backend) ───────────────────
    log_step(3, TOTAL_STEPS, "Selecting a SQL warehouse")
    warehouse_id = resolve_warehouse(
        w, cfg.warehouse, cfg.warehouse_id, cfg.non_interactive
    )
    cfg.warehouse_id = warehouse_id
    log_ok(f"Using warehouse {warehouse_id}")

    # ── Step 4: Unity Catalog setup ───────────────────────────────────────────
    log_step(4, TOTAL_STEPS, "Setting up Unity Catalog")
    named_by_flag = bool(cfg.catalog)
    cfg.catalog = resolve_catalog(w, cfg.catalog, cfg.non_interactive)
    log_info(f"Target: {cfg.catalog}.{cfg.schema}")
    setup_unity_catalog(
        w, warehouse_id, cfg.catalog, cfg.schema,
        confirm_missing=named_by_flag and can_prompt(cfg.non_interactive),
    )
    log_ok("Catalog, schema, and app_settings ready")

    # ── Step 5: upload app source (+ notebooks when present) ──────────────────
    log_step(5, TOTAL_STEPS, "Uploading app source to the workspace")
    ws_app_dir = _workspace_app_dir(user_email, cfg.app_name)
    n = upload_dir(w, app_dir, ws_app_dir)
    log_ok(f"Uploaded {n} app files to {ws_app_dir}")
    if notebooks_dir.exists():
        upload_dir(w, notebooks_dir, _notebooks_dir(user_email, cfg.app_name))

    # ── Step 6: data backend (lakebase provision OR warehouse skip) ───────────
    log_step(6, TOTAL_STEPS, f"Configuring the {cfg.data_backend} data backend")
    lakebase_host = cfg.lakebase_host
    if cfg.data_backend == "lakebase":
        project_id = sanitize_project_id(cfg.app_name)
        if lakebase_host:
            log_ok(f"Using provided Lakebase host: {lakebase_host}")
        else:
            lakebase_host = provision_lakebase(w, project_id)["host"]
        # Fatal on purpose: the app reads these tables, so shipping without them
        # would leave a running app that can never return data.
        try:
            conn_factory = _lakebase_conn_factory(
                w, project_id, lakebase_host, user_email
            )
            lakebase_create_db_and_tables(conn_factory, cfg.lakebase_db, lakebase_ddl())
            log_ok(f"Lakebase database '{cfg.lakebase_db}' and tables ready")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Could not prepare the Lakebase database '{cfg.lakebase_db}'.\n"
                f"    {exc}\n"
                f"    The app reads these tables, so the deploy stops here rather\n"
                f"    than leaving you a running app with no data. Re-run with\n"
                f"    --data-backend warehouse to skip Lakebase entirely."
            ) from exc
    else:
        log_info("Warehouse backend -- skipping Lakebase provisioning.")

    # ── Step 7: create app, upload its config, deploy ─────────────────────────
    log_step(7, TOTAL_STEPS, f"Creating and deploying app '{cfg.app_name}'")
    app = create_or_get_app(
        w, cfg.app_name, "Databricks Quest - gamification for platform adoption"
    )
    app_yaml = render_app_yaml(
        backend=cfg.data_backend,
        catalog=cfg.catalog,
        schema=cfg.schema,
        warehouse_id=warehouse_id,
        admins=cfg.admins,
        lakebase_host=lakebase_host if cfg.data_backend == "lakebase" else "",
        lakebase_db=cfg.lakebase_db,
    )
    # Upload the rendered config straight to the workspace copy rather than
    # writing it into the repo: no dirty working tree, no second full upload, and
    # two deploys from one checkout cannot race on the same local file.
    upload_text(w, app_yaml, f"{ws_app_dir}/app.yaml")
    deploy_app(w, cfg.app_name, ws_app_dir)
    log_ok("App source deployed")

    # ── Step 8: grants (UC always; Lakebase when applicable) ──────────────────
    log_step(8, TOTAL_STEPS, "Granting the app service principal access")
    sp = getattr(app, "service_principal_client_id", None)
    if sp:
        run_uc_statements(
            w, warehouse_id, uc_grant_statements(cfg.catalog, cfg.schema, sp)
        )
        log_ok(f"Granted UC access to SP {sp}")
        # CAN_USE on the warehouse: the app (running as the SP) needs it for the
        # warehouse backend and the runtime backend toggle. Applied on every deploy
        # with a warehouse, matching deploy.sh grant_sp_warehouse_access.
        try:
            grant_sp_warehouse(w, warehouse_id, sp)
            log_ok(f"Granted CAN_USE on warehouse {warehouse_id} to SP")
        except Exception as exc:  # noqa: BLE001
            log_warn(f"Warehouse CAN_USE grant: {str(exc)[:160]}")
        if cfg.data_backend == "lakebase" and lakebase_host:
            try:
                grant_sp_lakebase(
                    w, sanitize_project_id(cfg.app_name), sp,
                    lakebase_host, user_email, cfg.lakebase_db,
                )
                log_ok("Granted Lakebase access to SP")
            except Exception as exc:  # noqa: BLE001
                log_warn(f"Lakebase SP grant: {str(exc)[:160]}")
    else:
        log_warn("Could not resolve the app service principal -- grant access manually.")

    # ── Step 9: scoring pipeline + scheduled job ──────────────────────────────
    log_step(9, TOTAL_STEPS, "Scoring pipeline")
    graph = scoring_task_graph(
        _notebooks_dir(user_email, cfg.app_name),
        cfg.catalog, cfg.schema, warehouse_id, cfg.app_name,
        cfg.data_backend, lakebase_host or "", cfg.lakebase_db,
    )
    job_id = None
    try:
        job_id = create_scheduled_job(w, graph, cfg.app_name)
        log_ok(f"Scoring job {job_id} scheduled (every 4 hours, UTC)")
    except Exception as exc:  # noqa: BLE001
        log_warn(f"Scheduled job: {str(exc)[:160]}")
    if cfg.skip_scoring:
        log_warn("Skipping the initial scoring run (--skip-scoring).")
    elif job_id is not None:
        try:
            run = trigger_scoring(w, job_id)
            log_ok(f"Initial scoring run started (run {run.run_id})")
            log_info("First results appear once it finishes (typically 5-15 min).")
        except Exception as exc:  # noqa: BLE001
            log_warn(f"Initial scoring run: {str(exc)[:160]}")

    # ── Step 10: print the app URL ────────────────────────────────────────────
    log_step(10, TOTAL_STEPS, "Done")
    app = w.apps.get(name=cfg.app_name)
    url = getattr(app, "url", "") or ""
    state = getattr(getattr(app, "app_status", None), "state", "")
    log_ok(f"App: {cfg.app_name}  state={state}")
    if url:
        print(f"\n  {BOLD}Open the app:{NC} {CYAN}{url}{NC}\n")
    return 0


def _lakebase_conn_factory(w, project_id: str, host: str, user_email: str):
    """Return a ``(dbname) -> connection`` factory that mints a fresh Lakebase
    credential per call (tokens are short-lived)."""

    def _factory(dbname: str):
        token = _lakebase_credential(w, project_id)
        return lakebase_connect(host, user_email, token, dbname)

    return _factory


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. ``--help`` is handled by argparse (exit 0)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = _config_from_args(args)
    try:
        return run(cfg)
    except KeyboardInterrupt:
        log_err("Interrupted.")
        return 130
    except Exception as exc:  # noqa: BLE001
        log_err(f"Deploy failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
