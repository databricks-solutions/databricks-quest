"""Unit tests for the cross-platform ``deploy.py`` installer.

These tests exercise the pure helpers and the SDK client layer of ``deploy.py``
without touching a live Databricks workspace (the WorkspaceClient is mocked).
``deploy.py`` lives at the repo root, so it is loaded by file path rather than
as a package import.
"""

import importlib.util
import pathlib
import sys
from unittest.mock import MagicMock

spec = importlib.util.spec_from_file_location(
    "deploy", pathlib.Path(__file__).resolve().parent.parent / "deploy.py"
)
deploy = importlib.util.module_from_spec(spec)
# Register before exec_module: the standard importlib pattern. Required so the
# dataclass string annotations resolve, and so Task 5's ``"deploy" in
# sys.modules`` lazy-import assertion holds.
sys.modules["deploy"] = deploy
spec.loader.exec_module(deploy)


# ── Task 1: arg parsing ───────────────────────────────────────────────────────
def test_parser_defaults():
    args = deploy.build_parser().parse_args(["--catalog", "quest_data"])
    assert args.catalog == "quest_data"
    assert args.schema == "quest"
    assert args.app_name == "databricks-quest"
    assert args.data_backend == "lakebase"


def test_parser_backend_choice():
    args = deploy.build_parser().parse_args(
        ["--catalog", "c", "--data-backend", "warehouse"]
    )
    assert args.data_backend == "warehouse"


def test_parser_rejects_bad_backend():
    import pytest

    with pytest.raises(SystemExit):
        deploy.build_parser().parse_args(["--data-backend", "mysql"])


# ── Task 2: pure helpers (sanitize + app.yaml) ────────────────────────────────
def test_sanitize_project_id():
    assert deploy.sanitize_project_id("Databricks-Quest") == "databricks-quest"
    assert deploy.sanitize_project_id("My App 01!") == "my-app-01-"


def test_app_yaml_warehouse_backend_has_no_lakebase():
    y = deploy.render_app_yaml(
        backend="warehouse",
        catalog="quest_data",
        schema="quest",
        warehouse_id="wh123",
        admins="a@b.com",
    )
    assert "QUEST_DATA_BACKEND" in y and "warehouse" in y
    assert "QUEST_SQL_WAREHOUSE_ID" in y and "wh123" in y
    assert "QUEST_ADMIN_ALLOWLIST" in y and "a@b.com" in y
    # catalog/schema are emitted for every backend (runtime toggle needs them).
    assert "QUEST_CATALOG" in y and "QUEST_SCHEMA" in y
    assert "LAKEBASE_HOST" not in y
    assert "uvicorn" in y and "8000" in y


def test_app_yaml_lakebase_backend_has_lakebase():
    y = deploy.render_app_yaml(
        backend="lakebase",
        catalog="quest_data",
        schema="quest",
        warehouse_id="wh123",
        admins="a@b.com",
        lakebase_host="ep-x.database.cloud.databricks.com",
        lakebase_db="quest_db",
    )
    assert "LAKEBASE_HOST" in y and "ep-x.database.cloud.databricks.com" in y
    assert "LAKEBASE_DB" in y and "quest_db" in y
    assert "QUEST_DATA_BACKEND" in y and "lakebase" in y


# ── Task 3: SQL/DDL builders ──────────────────────────────────────────────────
def test_uc_setup_statements():
    stmts = deploy.uc_setup_statements("quest_data", "quest")
    joined = " ".join(stmts).lower()
    assert "create catalog if not exists quest_data" in joined
    assert "create schema if not exists quest_data.quest" in joined
    assert "app_settings" in joined


def test_uc_grant_includes_modify():
    stmts = deploy.uc_grant_statements("quest_data", "quest", "1234-sp")
    joined = " ".join(stmts)
    assert "USE CATALOG ON CATALOG quest_data" in joined
    assert "USE SCHEMA, SELECT, MODIFY ON SCHEMA quest_data.quest" in joined
    assert "1234-sp" in joined


def test_lakebase_ddl_has_six_tables():
    ddl = deploy.lakebase_ddl()
    for t in [
        "mission_completions",
        "user_points_fact",
        "user_profile_snapshot",
        "leaderboard",
        "badges",
        "notifications",
    ]:
        assert t in ddl


def test_lakebase_grant_statements_include_write_path():
    # deploy.sh:250-256 grants read AND the app_settings write path so the runtime
    # backend toggle can persist. Assert both halves are present for the SP.
    stmts = deploy.lakebase_grant_statements("1234-sp")
    joined = " ".join(stmts)
    # read grants
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "1234-sp"' in joined
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public" in joined
    # write path (I-1): app_settings table + CREATE on schema + write on the table
    assert (
        "CREATE TABLE IF NOT EXISTS app_settings "
        "(key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP)" in joined
    )
    assert 'GRANT CREATE ON SCHEMA public TO "1234-sp"' in joined
    assert 'GRANT SELECT, INSERT, UPDATE ON app_settings TO "1234-sp"' in joined


# ── Task 4: SDK client layer (mocked -- no live calls) ────────────────────────
def test_resolve_warehouse_by_id_passthrough():
    w = MagicMock()
    assert (
        deploy.resolve_warehouse(w, name=None, wid="wh-explicit", non_interactive=True)
        == "wh-explicit"
    )
    w.warehouses.list.assert_not_called()


def test_resolve_warehouse_by_name():
    w = MagicMock()
    wh = MagicMock()
    wh.name = "My WH"
    wh.id = "wh-42"
    w.warehouses.list.return_value = [wh]
    assert (
        deploy.resolve_warehouse(w, name="My WH", wid=None, non_interactive=True)
        == "wh-42"
    )


def test_run_uc_statements_calls_execute():
    w = MagicMock()
    deploy.run_uc_statements(
        w, "wh1", "quest_data", ["CREATE CATALOG IF NOT EXISTS quest_data"]
    )
    assert w.statement_execution.execute_statement.called


def test_grant_sp_warehouse_uses_can_use():
    # I-2: the app SP must get CAN_USE on the warehouse (it runs as the SP).
    from databricks.sdk.service.sql import WarehousePermissionLevel

    w = MagicMock()
    deploy.grant_sp_warehouse(w, "wh-99", "1234-sp")
    w.warehouses.update_permissions.assert_called_once()
    kwargs = w.warehouses.update_permissions.call_args.kwargs
    assert kwargs["warehouse_id"] == "wh-99"
    acl = kwargs["access_control_list"]
    assert len(acl) == 1
    assert acl[0].service_principal_name == "1234-sp"
    assert acl[0].permission_level == WarehousePermissionLevel.CAN_USE


# ── Task 5: Lakebase provisioning (mock SDK + psycopg2, no real DB) ───────────
def test_provision_lakebase_creates_instance_when_missing():
    # sdk 0.55: DatabaseInstance is in .catalog and the API group is
    # w.database_instances (NOT w.database). The readiness signal is
    # state == AVAILABLE with a read_write_dns host.
    w = MagicMock()
    inst = MagicMock()
    inst.read_write_dns = "ep-x.database.cloud.databricks.com"
    inst.state = "AVAILABLE"
    w.database_instances.create_database_instance.return_value = inst
    # miss (not found) on first get, then hit on the poll
    w.database_instances.get_database_instance.side_effect = [Exception("nf"), inst]
    out = deploy.provision_lakebase(w, "databricks-quest")
    assert w.database_instances.create_database_instance.called
    assert out["host"] == "ep-x.database.cloud.databricks.com"


def test_lakebase_credential_raises_clean_when_method_missing():
    # sdk 0.55's DatabaseInstancesAPI has no generate_database_credential. Use a
    # spec-constrained mock so the missing method is not auto-created; the helper
    # must raise a clear RuntimeError (not a bare AttributeError) in that case.
    import pytest
    from databricks.sdk.service.catalog import DatabaseInstancesAPI

    w = MagicMock()
    w.database_instances = MagicMock(spec=DatabaseInstancesAPI)
    with pytest.raises(RuntimeError, match="generate_database_credential"):
        deploy._lakebase_credential(w, "databricks-quest")


def test_lakebase_credential_uses_method_when_present():
    # Forward-compatible: when the SDK exposes the method, the token is returned.
    w = MagicMock()
    w.database_instances.generate_database_credential.return_value = MagicMock(token="tok-123")
    assert deploy._lakebase_credential(w, "databricks-quest") == "tok-123"


def test_lakebase_import_is_lazy():
    # psycopg2 must NOT be imported at module load (so warehouse-mode never needs it)
    assert "deploy" in sys.modules  # module loaded
    # provision functions import psycopg2 lazily inside the function body
    import ast

    src = pathlib.Path(deploy.__file__).read_text()
    tree = ast.parse(src)
    top_imports = [
        n
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for n in getattr(node, "names", [])
    ]
    assert not any(
        "psycopg2" in (n.name or "") for n in top_imports
    ), "psycopg2 must be imported lazily, not at top level"


# ── Task 6: orchestration + cross-platform guard ─────────────────────────────
def test_no_posix_only_or_shell():
    src = pathlib.Path(deploy.__file__).read_text()
    assert "os.system(" not in src
    assert "psql" not in src.lower()
    assert "/bin/bash" not in src and "shell=True" not in src


def test_main_help_exits_zero():
    # --help is handled by argparse and exits 0 without running the deploy flow.
    import pytest

    with pytest.raises(SystemExit) as e:
        deploy.main(["--help"])
    assert e.value.code == 0


def test_main_dispatches_steps(monkeypatch):
    # main() parses args into a Config and dispatches to run(); stub run() so no
    # live client is touched and assert the parsed options are threaded through.
    captured = {}

    def fake_run(cfg):
        captured["cfg"] = cfg
        return 0

    monkeypatch.setattr(deploy, "run", fake_run)
    rc = deploy.main(["--catalog", "quest_data", "--data-backend", "warehouse"])
    assert rc == 0
    assert captured["cfg"].catalog == "quest_data"
    assert captured["cfg"].data_backend == "warehouse"
