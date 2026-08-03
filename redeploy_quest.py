#!/usr/bin/env python3
"""Databricks Quest -- clean teardown + fresh warehouse-only redeploy.

Deletes everything a previous Quest deploy created in a workspace, then stands
Quest back up in **SQL-warehouse-only** mode by driving the repo's own
``deploy.py``. Cross-platform (Windows/macOS/Linux), SDK-driven -- no bash, no
psql, no Terraform. Use it to reset a workspace to a known-good Quest install.

What it deletes (matched by the exact names ``deploy.py`` uses, so it only ever
touches Quest's own resources):
    - Databricks Apps whose name is the target app (or, with --all, every app
      whose name looks like a Quest app).
    - Scoring jobs named ``[Quest] Scoring Pipeline (<app>)``.
    - Lakebase database instances named after the app's project id
      (``sanitize_project_id(app_name)``) -- left over from any prior Lakebase
      deploy.
    - The Quest schema(s) ``<catalog>.<schema>`` (dropped with CASCADE).
It never drops the catalog itself, and never touches non-Quest resources.

Then it redeploys with::

    python deploy.py --catalog <catalog> --data-backend warehouse -y

and verifies the app's service principal has the Unity Catalog + warehouse
grants it needs to read the scored tables through the warehouse.

Usage:
    python redeploy_quest.py --profile my-ws --catalog quest_data
    python redeploy_quest.py --profile my-ws --catalog quest_data --all --yes
    python redeploy_quest.py --profile my-ws --catalog quest_data --teardown-only

Requirements: same as deploy.py (databricks-sdk, PyYAML). psycopg2 not needed
for warehouse-only. A Databricks CLI login or DATABRICKS_HOST/TOKEN env.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

# Reuse deploy.py's own helpers so names/logging never drift from the real deploy.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import deploy  # noqa: E402  (deploy.py sits next to this script)

from deploy import (  # noqa: E402
    BOLD,
    CYAN,
    NC,
    YELLOW,
    get_client,
    job_name,
    log_info,
    log_ok,
    log_step,
    log_warn,
    log_err,
    sanitize_project_id,
    uc_grant_statements,
)


# ── Discovery ─────────────────────────────────────────────────────────────────
def find_quest_apps(w, app_name: str, match_all: bool) -> List[str]:
    """App names to delete.

    Default: just ``app_name``. With ``--all``: every app whose name is exactly
    'databricks-quest' or starts with 'quest' / 'databricks-quest' -- the naming
    deploy.py produces. Listed defensively so an apps.list RPC hiccup on some
    shards does not abort the whole teardown.
    """
    try:
        names = [a.name for a in w.apps.list() if a.name]
    except Exception as exc:  # noqa: BLE001
        log_warn(f"Could not list apps ({str(exc)[:120]}); will try '{app_name}' by name.")
        names = [app_name]
    if match_all:
        hits = [
            n for n in names
            if n == "databricks-quest" or n.startswith("quest") or n.startswith("databricks-quest")
        ]
        return sorted(set(hits))
    return [n for n in names if n == app_name]


def find_quest_jobs(w) -> List[tuple]:
    """(job_id, name) for every scoring job named '[Quest] Scoring Pipeline (...)'."""
    out = []
    for j in w.jobs.list():
        nm = j.settings.name if j.settings else None
        if nm and nm.startswith("[Quest] Scoring Pipeline"):
            out.append((j.job_id, nm))
    return out


def _database_api(w):
    """The SDK group that owns Lakebase instances (moved across SDK releases)."""
    return getattr(w, "database", None) or getattr(w, "database_instances", None)


def find_quest_lakebase(w, app_names: List[str], match_all: bool) -> List[str]:
    """Lakebase instance names to delete.

    deploy.py names the instance ``sanitize_project_id(app_name)``. We map the
    target app names to their project ids; with --all we also sweep any instance
    whose name looks like a Quest project id.
    """
    api = _database_api(w)
    if api is None or not hasattr(api, "list_database_instances"):
        return []
    try:
        existing = [i.name for i in api.list_database_instances() if getattr(i, "name", None)]
    except Exception as exc:  # noqa: BLE001
        log_warn(f"Could not list Lakebase instances ({str(exc)[:120]}).")
        return []
    wanted = {sanitize_project_id(a) for a in app_names}
    if match_all:
        return sorted(n for n in existing if n == "databricks-quest" or n.startswith("quest") or n.startswith("databricks-quest"))
    return sorted(n for n in existing if n in wanted)


# ── Teardown ────────────────────────────────────────────────────────────────
def delete_apps(w, names: List[str]) -> None:
    for n in names:
        try:
            w.apps.delete(name=n)
            log_ok(f"Deleted app '{n}'")
        except Exception as exc:  # noqa: BLE001
            log_warn(f"App '{n}': {str(exc)[:140]}")


def delete_jobs(w, jobs: List[tuple]) -> None:
    for job_id, nm in jobs:
        try:
            w.jobs.delete(job_id=job_id)
            log_ok(f"Deleted job {job_id}  ({nm})")
        except Exception as exc:  # noqa: BLE001
            log_warn(f"Job {job_id}: {str(exc)[:140]}")


def delete_lakebase(w, names: List[str]) -> None:
    api = _database_api(w)
    if api is None:
        return
    for n in names:
        try:
            # purge=True removes the instance and its child databases. Some
            # workspaces reject the `force` flag ("Force is not supported for
            # database instance deletion"), so try purge-only first and only add
            # force if the server actually asks for it.
            try:
                api.delete_database_instance(name=n, purge=True)
            except Exception as first:  # noqa: BLE001
                if "force" in str(first).lower():
                    api.delete_database_instance(name=n)
                else:
                    raise
            log_ok(f"Deleted Lakebase instance '{n}'")
        except Exception as exc:  # noqa: BLE001
            log_warn(f"Lakebase '{n}': {str(exc)[:140]}")


def drop_quest_schemas(w, warehouse_id: Optional[str], catalog: str, schemas: List[str]) -> None:
    """Drop the Quest schema(s) with CASCADE via the warehouse.

    Needs a warehouse to run SQL. If none is given/found we skip and say so --
    the fresh deploy will re-create the schema anyway; leftover tables would just
    be overwritten, so this is a cleanliness step, not a correctness one.
    """
    if not warehouse_id:
        log_warn("No warehouse available to drop schemas; skipping (deploy will recreate).")
        return
    for s in schemas:
        stmt = f"DROP SCHEMA IF EXISTS {catalog}.{s} CASCADE"
        try:
            deploy.run_uc_statements(w, warehouse_id, [stmt])
            log_ok(f"Dropped schema {catalog}.{s}")
        except Exception as exc:  # noqa: BLE001
            log_warn(f"Drop {catalog}.{s}: {str(exc)[:140]}")


def _first_running_warehouse_id(w) -> Optional[str]:
    """Any warehouse id we can use to run the DROP SCHEMA statements.

    Prefer a RUNNING one to avoid cold-start latency; fall back to the first
    warehouse otherwise. Returns None if the workspace has none.
    """
    first = None
    for wh in w.warehouses.list():
        first = first or wh.id
        state = getattr(getattr(wh, "state", None), "value", None)
        if state == "RUNNING":
            return wh.id
    return first


# ── Redeploy (drive the repo's own deploy.py) ─────────────────────────────────
def run_deploy(profile: Optional[str], catalog: str, schema: str, app_name: str,
               warehouse_id: Optional[str], admins: str) -> int:
    """Invoke deploy.py in warehouse-only, non-interactive mode."""
    cmd = [
        sys.executable, str(_HERE / "deploy.py"),
        "--catalog", catalog,
        "--schema", schema,
        "--app-name", app_name,
        "--data-backend", "warehouse",
        "--non-interactive",
    ]
    if profile:
        cmd += ["--profile", profile]
    if warehouse_id:
        cmd += ["--warehouse-id", warehouse_id]
    if admins:
        cmd += ["--admins", admins]
    print(f"\n{CYAN}$ {' '.join(cmd)}{NC}\n", flush=True)
    return subprocess.call(cmd)


# ── Post-deploy permission verification ───────────────────────────────────────
def verify_permissions(w, app_name: str, catalog: str, schema: str) -> bool:
    """Confirm the app SP can read the scored tables through the warehouse.

    Checks the exact grants deploy.py applies (uc_grant_statements +
    grant_sp_warehouse): USE CATALOG on the catalog, USE SCHEMA + SELECT + MODIFY
    on the schema, and CAN_USE on the warehouse the app is wired to. Then does a
    live read of app_settings via that warehouse as a smoke test.
    """
    ok = True

    # 1. Resolve the app + its service principal + its wired warehouse id.
    app = w.apps.get(name=app_name)
    sp = getattr(app, "service_principal_client_id", None)
    if not sp:
        log_err("App has no service principal client id yet; cannot verify grants.")
        return False
    log_info(f"App SP: {sp}")

    wid = None
    for r in (getattr(app, "resources", None) or []):
        wh = getattr(r, "sql_warehouse", None)
        if wh and getattr(wh, "id", None):
            wid = wh.id
    if not wid:
        wid = _first_running_warehouse_id(w)
    log_info(f"Warehouse for verification: {wid}")

    # 2. Unity Catalog grants on the schema (SELECT + MODIFY + USE SCHEMA) and catalog.
    need_schema = {"SELECT", "MODIFY", "USE_SCHEMA"}
    need_catalog = {"USE_CATALOG"}
    try:
        eff = w.grants.get_effective(
            securable_type="schema", full_name=f"{catalog}.{schema}", principal=sp
        )
        have = set()
        for a in (eff.privilege_assignments or []):
            for pv in (a.privileges or []):
                # Each entry may be an EffectivePrivilege (has .privilege) or a bare enum.
                priv = getattr(pv, "privilege", pv)
                have.add(getattr(priv, "value", priv))
        missing = need_schema - have
        if missing:
            ok = False
            log_err(f"Schema {catalog}.{schema}: SP missing {sorted(missing)} (has {sorted(have)})")
        else:
            log_ok(f"Schema {catalog}.{schema}: SP has USE SCHEMA + SELECT + MODIFY")
    except Exception as exc:  # noqa: BLE001
        log_warn(f"Could not read schema grants ({str(exc)[:120]}); falling back to live read test.")

    # 3. CAN_USE on the warehouse.
    if wid:
        try:
            perms = w.warehouses.get_permissions(warehouse_id=wid)
            sp_can_use = any(
                (getattr(acl, "service_principal_name", None) == sp)
                and any(getattr(p, "permission_level", None)
                        and getattr(p.permission_level, "value", "") in ("CAN_USE", "CAN_MANAGE")
                        for p in (acl.all_permissions or []))
                for acl in (perms.access_control_list or [])
            )
            if sp_can_use:
                log_ok(f"Warehouse {wid}: SP has CAN_USE")
            else:
                ok = False
                log_err(f"Warehouse {wid}: SP does NOT have CAN_USE")
        except Exception as exc:  # noqa: BLE001
            log_warn(f"Could not read warehouse permissions ({str(exc)[:120]}).")

    # 4. Live smoke test: read app_settings through the warehouse (what the app does).
    if wid:
        try:
            resp = w.statement_execution.execute_statement(
                warehouse_id=wid,
                statement=f"SELECT count(*) AS c FROM {catalog}.{schema}.app_settings",
                wait_timeout="50s",
            )
            state = getattr(getattr(resp, "status", None), "state", None)
            state = getattr(state, "value", state)
            if state == "SUCCEEDED":
                log_ok("Live read of app_settings through the warehouse SUCCEEDED")
            else:
                ok = False
                log_err(f"Live warehouse read did not succeed (state={state}).")
        except Exception as exc:  # noqa: BLE001
            ok = False
            log_err(f"Live warehouse read failed: {str(exc)[:160]}")

    # 5. Re-assert the grants if anything was missing -- idempotent, mirrors deploy.py.
    if not ok:
        log_warn("Re-applying UC grants to close the gap (idempotent)...")
        try:
            deploy.run_uc_statements(w, wid, uc_grant_statements(catalog, schema, sp))
            deploy.grant_sp_warehouse(w, wid, sp)
            log_ok("Re-applied UC + warehouse grants; re-run verification to confirm.")
        except Exception as exc:  # noqa: BLE001
            log_err(f"Re-grant failed: {str(exc)[:160]}")
    return ok


# ── CLI ───────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="redeploy_quest.py",
        description="Delete existing Quest resources, then redeploy warehouse-only.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--profile", default=None, help="Databricks CLI profile.")
    p.add_argument("--host", default=None, help="Workspace URL (if not using a profile).")
    p.add_argument("--catalog", default="", help="Unity Catalog for Quest data (required for redeploy).")
    p.add_argument("--schema", default="quest", help="Schema for Quest tables.")
    p.add_argument("--app-name", default="databricks-quest", help="Databricks App name.")
    p.add_argument("--warehouse-id", default=None, help="Use this warehouse (else auto-pick).")
    p.add_argument("--admins", default="", help="Comma-separated extra Admin-page admins.")
    p.add_argument("--all", action="store_true",
                   help="Delete EVERY Quest-looking app / job / Lakebase instance, not just --app-name.")
    p.add_argument("--teardown-only", action="store_true", help="Delete, then stop (no redeploy).")
    p.add_argument("--yes", "-y", action="store_true", help="Do not prompt before deleting.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.teardown_only and not args.catalog:
        log_err("--catalog is required unless --teardown-only.")
        return 2

    w = get_client(args.profile, args.host)
    me = w.current_user.me()
    log_ok(f"Connected as {getattr(me, 'user_name', '?')}")

    # ── Discover what exists ──────────────────────────────────────────────────
    log_step(1, 4 if not args.teardown_only else 2, "Finding existing Quest resources")
    apps = find_quest_apps(w, args.app_name, args.all)
    jobs = find_quest_jobs(w)
    # When --all, also target Lakebase for every discovered app; else just this app.
    lb_app_names = apps if args.all else [args.app_name]
    lakebase = find_quest_lakebase(w, lb_app_names, args.all)
    # Schemas: with --all, sweep every quest* schema in the catalog; else just --schema.
    schemas = [args.schema]
    if args.all and args.catalog:
        try:
            schemas = sorted({s.name for s in w.schemas.list(catalog_name=args.catalog)
                              if s.name.startswith("quest")})
        except Exception:  # noqa: BLE001
            pass

    print(f"\n{BOLD}Will delete:{NC}")
    print(f"  Apps ({len(apps)}):       {apps or '(none)'}")
    print(f"  Jobs ({len(jobs)}):       {[j[1] for j in jobs] or '(none)'}")
    print(f"  Lakebase ({len(lakebase)}):   {lakebase or '(none)'}")
    print(f"  Schemas ({len(schemas)}):    {[f'{args.catalog}.{s}' for s in schemas] if args.catalog else '(none)'}")

    if not (apps or jobs or lakebase):
        log_info("No existing Quest apps/jobs/Lakebase found.")

    if not args.yes:
        try:
            ans = input(f"\n{YELLOW}Proceed with deletion? [y/N]: {NC}").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("y", "yes"):
            log_warn("Aborted; nothing deleted.")
            return 1

    # ── Teardown ──────────────────────────────────────────────────────────────
    log_step(2, 4 if not args.teardown_only else 2, "Deleting existing Quest resources")
    wid_for_drop = args.warehouse_id or _first_running_warehouse_id(w)
    delete_jobs(w, jobs)          # jobs first: stop scoring writing mid-teardown
    delete_apps(w, apps)
    delete_lakebase(w, lakebase)
    if args.catalog:
        drop_quest_schemas(w, wid_for_drop, args.catalog, schemas)
    log_ok("Teardown complete.")

    if args.teardown_only:
        return 0

    # ── Redeploy (warehouse-only) ─────────────────────────────────────────────
    log_step(3, 4, "Redeploying Quest (SQL warehouse only)")
    rc = run_deploy(args.profile, args.catalog, args.schema, args.app_name,
                    args.warehouse_id, args.admins)
    if rc != 0:
        log_err(f"deploy.py exited {rc}; skipping verification.")
        return rc

    # ── Verify the app can read UC scoring data through the warehouse ─────────
    log_step(4, 4, "Verifying app -> Unity Catalog (via warehouse) permissions")
    ok = verify_permissions(w, args.app_name, args.catalog, args.schema)
    if ok:
        log_ok("Permissions verified: the app can read the scored tables via the warehouse.")
    else:
        log_warn("Permission gaps were found (and a re-grant attempted). Re-run to confirm.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
