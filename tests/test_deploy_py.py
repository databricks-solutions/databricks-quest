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

import pytest

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
def test_uc_schema_statements():
    stmts = deploy.uc_schema_statements("quest_data", "quest")
    joined = " ".join(stmts).lower()
    assert "create schema if not exists quest_data.quest" in joined
    assert "app_settings" in joined
    # training_completions must be pre-created here too, so the warehouse-backend
    # tick-box (which MERGEs into it) works before the scoring job's first run and
    # the app SP never needs CREATE TABLE. Regression: a real warehouse deploy hit
    # TABLE_OR_VIEW_NOT_FOUND on the first tick when this was missing.
    assert "training_completions" in joined
    # The catalog is only created on demand, never as part of the schema step.
    assert "create catalog" not in joined


def test_setup_unity_catalog_skips_catalog_creation_when_schema_works():
    # Most deploying identities have CREATE SCHEMA on an existing catalog but not
    # metastore-level CREATE CATALOG, so the catalog must not be touched.
    w = MagicMock()
    deploy.setup_unity_catalog(w, "wh1", "quest_data", "quest")
    issued = " ".join(
        c.kwargs["statement"]
        for c in w.statement_execution.execute_statement.call_args_list
    ).lower()
    assert "create catalog" not in issued
    assert "create schema if not exists quest_data.quest" in issued


def test_setup_unity_catalog_creates_catalog_only_when_missing(monkeypatch):
    calls = []

    def fake_run(w, warehouse_id, statements):
        calls.append(list(statements))
        # First schema attempt fails with the real UC error; then succeed.
        if len(calls) == 1:
            raise RuntimeError(
                "UC statement failed (FAILED): [NO_SUCH_CATALOG_EXCEPTION] "
                "Catalog 'quest_data' was not found."
            )

    monkeypatch.setattr(deploy, "run_uc_statements", fake_run)
    deploy.setup_unity_catalog(MagicMock(), "wh1", "quest_data", "quest")
    assert len(calls) == 3
    assert "CREATE CATALOG IF NOT EXISTS quest_data" in calls[1]
    assert any("CREATE SCHEMA" in s for s in calls[2])


def test_setup_unity_catalog_reraises_non_catalog_errors(monkeypatch):
    import pytest

    def fake_run(w, warehouse_id, statements):
        raise RuntimeError("UC statement failed (FAILED): PERMISSION_DENIED")

    monkeypatch.setattr(deploy, "run_uc_statements", fake_run)
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        deploy.setup_unity_catalog(MagicMock(), "wh1", "quest_data", "quest")


def _stdin(monkeypatch, tty: bool):
    class Fake:
        def isatty(self):
            return tty

    monkeypatch.setattr(deploy.sys, "stdin", Fake())


def _answers(monkeypatch, *replies):
    it = iter(replies)
    monkeypatch.setattr("builtins.input", lambda *_: next(it))


def _catalog_client(*names):
    def catalog(n):
        c = MagicMock()
        c.name = n
        return c

    w = MagicMock()
    w.catalogs.list.return_value = [catalog(n) for n in names]
    return w


def test_resolve_catalog_respects_an_explicit_flag(monkeypatch):
    # --catalog means "use this"; never second-guess it with a prompt.
    def explode(*a, **k):
        raise AssertionError("should not prompt when --catalog was given")

    monkeypatch.setattr("builtins.input", explode)
    w = MagicMock()
    assert deploy.resolve_catalog(w, "acme_data", non_interactive=False) == "acme_data"
    w.catalogs.list.assert_not_called()


def test_resolve_catalog_offers_existing_catalogs(monkeypatch):
    _stdin(monkeypatch, True)
    _answers(monkeypatch, "2")
    w = _catalog_client("acme_data", "sandbox", "system")
    assert deploy.resolve_catalog(w, None, non_interactive=False) == "sandbox"


def test_resolve_catalog_defaults_to_the_first_option(monkeypatch):
    _stdin(monkeypatch, True)
    _answers(monkeypatch, "")  # bare Enter
    w = _catalog_client("acme_data", "sandbox")
    assert deploy.resolve_catalog(w, None, non_interactive=False) == "acme_data"


def test_resolve_catalog_can_create_a_new_one(monkeypatch):
    _stdin(monkeypatch, True)
    _answers(monkeypatch, "n", "quest_prod")
    w = _catalog_client("acme_data")
    assert deploy.resolve_catalog(w, None, non_interactive=False) == "quest_prod"


def test_resolve_catalog_new_name_has_a_default(monkeypatch):
    _stdin(monkeypatch, True)
    _answers(monkeypatch, "n", "")
    w = _catalog_client("acme_data")
    assert (
        deploy.resolve_catalog(w, None, non_interactive=False)
        == deploy.DEFAULT_NEW_CATALOG
    )


def test_resolve_catalog_prompts_for_a_name_when_none_exist(monkeypatch):
    # No existing catalogs means there is nothing to choose between.
    _stdin(monkeypatch, True)
    _answers(monkeypatch, "brand_new")
    w = _catalog_client("system", "samples")
    assert deploy.resolve_catalog(w, None, non_interactive=False) == "brand_new"


def test_resolve_catalog_rejects_a_bad_selection(monkeypatch):
    import pytest

    _stdin(monkeypatch, True)
    _answers(monkeypatch, "9")
    w = _catalog_client("acme_data")
    with pytest.raises(RuntimeError, match="Invalid catalog selection"):
        deploy.resolve_catalog(w, None, non_interactive=False)


def test_resolve_catalog_still_requires_the_flag_non_interactively(monkeypatch):
    import pytest

    _stdin(monkeypatch, False)
    with pytest.raises(RuntimeError, match="--catalog is required"):
        deploy.resolve_catalog(MagicMock(), None, non_interactive=True)


def _missing_then_ok(monkeypatch):
    calls = []

    def fake_run(w, warehouse_id, statements):
        calls.append(list(statements))
        if len(calls) == 1:
            raise RuntimeError("[NO_SUCH_CATALOG_EXCEPTION] Catalog not found")

    monkeypatch.setattr(deploy, "run_uc_statements", fake_run)
    return calls


def test_setup_unity_catalog_confirms_a_flag_named_catalog(monkeypatch):
    # A typo in --catalog should not silently create a second catalog.
    import pytest

    _answers(monkeypatch, "n")
    _missing_then_ok(monkeypatch)
    with pytest.raises(RuntimeError, match="was not created"):
        deploy.setup_unity_catalog(
            MagicMock(), "wh1", "typoed_catalog", "quest", confirm_missing=True
        )


def test_setup_unity_catalog_creates_after_confirmation(monkeypatch):
    _answers(monkeypatch, "")  # bare Enter accepts the [Y/n] default
    calls = _missing_then_ok(monkeypatch)
    deploy.setup_unity_catalog(
        MagicMock(), "wh1", "quest_data", "quest", confirm_missing=True
    )
    assert "CREATE CATALOG IF NOT EXISTS quest_data" in calls[1]


def test_setup_unity_catalog_does_not_double_ask(monkeypatch):
    # Someone who just chose "create a new catalog" already answered this.
    def explode(*a, **k):
        raise AssertionError("should not ask twice")

    monkeypatch.setattr("builtins.input", explode)
    calls = _missing_then_ok(monkeypatch)
    deploy.setup_unity_catalog(
        MagicMock(), "wh1", "quest_data", "quest", confirm_missing=False
    )
    assert "CREATE CATALOG IF NOT EXISTS quest_data" in calls[1]


def test_suggest_catalogs_hides_builtins():
    def catalog(name):
        c = MagicMock()
        c.name = name  # set after construction; MagicMock(name=...) is special
        return c

    w = MagicMock()
    w.catalogs.list.return_value = [
        catalog(n)
        for n in ["system", "samples", "hive_metastore", "acme_data", "sandbox"]
    ]
    assert deploy.suggest_catalogs(w) == ["acme_data", "sandbox"]


def test_suggest_catalogs_survives_a_listing_failure():
    w = MagicMock()
    w.catalogs.list.side_effect = Exception("permission denied")
    assert deploy.suggest_catalogs(w) == []


def test_catalog_failure_names_catalogs_the_user_can_use(monkeypatch):
    # Someone new to Databricks has no idea what to pass instead, so the error
    # has to name real options rather than just saying no.
    import pytest

    def fake_run(w, warehouse_id, statements):
        if any("CREATE CATALOG" in s for s in statements):
            raise RuntimeError("PERMISSION_DENIED: no CREATE CATALOG on Metastore")
        raise RuntimeError("[NO_SUCH_CATALOG_EXCEPTION] Catalog not found")

    monkeypatch.setattr(deploy, "run_uc_statements", fake_run)
    monkeypatch.setattr(deploy, "suggest_catalogs", lambda w: ["acme_data", "sandbox"])
    with pytest.raises(RuntimeError) as e:
        deploy.setup_unity_catalog(MagicMock(), "wh1", "quest_data", "quest")
    msg = str(e.value)
    assert "acme_data" in msg and "sandbox" in msg
    assert "--catalog acme_data" in msg  # a command they can copy


def test_setup_unity_catalog_explains_default_storage_failure(monkeypatch):
    # Accounts on Default Storage reject a bare CREATE CATALOG; the deployer must
    # say what to do instead of surfacing the raw metastore error.
    import pytest

    def fake_run(w, warehouse_id, statements):
        if any("CREATE CATALOG" in s for s in statements):
            raise RuntimeError("Metastore storage root URL does not exist")
        raise RuntimeError("[NO_SUCH_CATALOG_EXCEPTION] Catalog not found")

    monkeypatch.setattr(deploy, "run_uc_statements", fake_run)
    monkeypatch.setattr(deploy, "suggest_catalogs", lambda w: [])
    with pytest.raises(RuntimeError, match="metastore admin"):
        deploy.setup_unity_catalog(MagicMock(), "wh1", "quest_data", "quest")


def test_uc_grant_includes_modify():
    stmts = deploy.uc_grant_statements("quest_data", "quest", "1234-sp")
    joined = " ".join(stmts)
    assert "USE CATALOG ON CATALOG quest_data" in joined
    assert "USE SCHEMA, SELECT, MODIFY ON SCHEMA quest_data.quest" in joined
    assert "1234-sp" in joined


def test_lakebase_ddl_has_every_table_the_app_and_job_need():
    ddl = deploy.lakebase_ddl()
    for t in [
        "mission_completions",
        "user_points_fact",
        "user_profile_snapshot",
        "leaderboard",
        "badges",
        "notifications",
        # Backend toggle + course ticks. Omitting training_attestations fails the
        # whole scoring job: roundtrip_attestations selects from it every run.
        "app_settings",
        "training_attestations",
    ]:
        assert t in ddl, f"{t} missing from Lakebase DDL"


def test_lakebase_grant_statements_include_write_path():
    stmts = deploy.lakebase_grant_statements("1234-sp")
    joined = " ".join(stmts)
    # read grants
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "1234-sp"' in joined
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public" in joined
    assert 'GRANT CREATE ON SCHEMA public TO "1234-sp"' in joined
    # Explicit writes on every table the app writes at runtime: the backend
    # toggle, the course-tick record, and the four instant-award serving tables.
    for table in (
        "app_settings",
        "training_attestations",
        "mission_completions",
        "user_points_fact",
        "user_profile_snapshot",
        "leaderboard",
    ):
        assert f'GRANT SELECT, INSERT, UPDATE ON {table} TO "1234-sp"' in joined


def test_lakebase_grants_cover_the_instant_award_transaction():
    # The /api/training/attest handler runs one atomic transaction that writes
    # training_attestations AND the four serving tables the instant award touches
    # (mission_completions + user_points_fact via _award_mission_tx, then
    # user_profile_snapshot + leaderboard via _bump_user_totals_tx). deploy.py
    # drops deploy.sh's DATABRICKS_SUPERUSER membership, so if any of these lacks
    # an explicit write grant the whole transaction rolls back with a permission
    # error and the tick-box 503s. Guard every table the award path writes.
    award_tables = {
        "training_attestations",
        "mission_completions",
        "user_points_fact",
        "user_profile_snapshot",
        "leaderboard",
    }
    assert award_tables.issubset(set(deploy.APP_WRITABLE_TABLES))


def test_sp_role_conflict_on_redeploy_is_silent(capsys, monkeypatch):
    # Re-running a deploy re-registers the SP role; the API calls that a conflict,
    # and warning about it every time would look like a real problem.
    w = MagicMock()
    w.database.create_database_instance_role.side_effect = Exception(
        "Requested role conflicts with existing role '1234-sp'"
    )
    monkeypatch.setattr(deploy, "_lakebase_credential", lambda *a, **k: "tok")
    monkeypatch.setattr(deploy, "lakebase_connect", lambda *a, **k: MagicMock())
    deploy.grant_sp_lakebase(w, "proj", "1234-sp", "host", "me@x.com", "quest_db")
    assert "role registration" not in capsys.readouterr().out


def test_every_writable_table_exists_in_the_ddl():
    # A grant on a table the DDL never creates fails at deploy time.
    ddl = deploy.lakebase_ddl()
    for table in deploy.APP_WRITABLE_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl


# ── Task 4: SDK client layer (mocked -- no live calls) ────────────────────────
def test_resolve_warehouse_by_id_passthrough():
    w = MagicMock()
    assert (
        deploy.resolve_warehouse(w, name=None, wid="wh-explicit", non_interactive=True)
        == "wh-explicit"
    )
    w.warehouses.list.assert_not_called()


def test_resolve_warehouse_defaults_when_stdin_is_not_a_tty(monkeypatch, capsys):
    # Piping output or running in CI closes stdin. Prompting there aborts the
    # whole deploy with a bare "EOF when reading a line".
    class NoTty:
        def isatty(self):
            return False

    def explode(*a, **k):
        raise AssertionError("must not prompt without a terminal")

    monkeypatch.setattr(deploy.sys, "stdin", NoTty())
    monkeypatch.setattr("builtins.input", explode)
    w = MagicMock()
    wh = MagicMock()
    wh.name, wh.id = "Starter", "wh-1"
    w.warehouses.list.return_value = [wh]
    assert deploy.resolve_warehouse(w, None, None, non_interactive=False) == "wh-1"
    out = capsys.readouterr().out
    assert "No interactive terminal" in out
    assert "--warehouse" in out  # tells the user how to pick another


def test_resolve_warehouse_prompts_when_attached_to_a_terminal(monkeypatch):
    class Tty:
        def isatty(self):
            return True

    monkeypatch.setattr(deploy.sys, "stdin", Tty())
    monkeypatch.setattr("builtins.input", lambda *_: "2")
    w = MagicMock()
    a, b = MagicMock(), MagicMock()
    a.name, a.id = "One", "wh-1"
    b.name, b.id = "Two", "wh-2"
    w.warehouses.list.return_value = [a, b]
    assert deploy.resolve_warehouse(w, None, None, non_interactive=False) == "wh-2"


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
    deploy.run_uc_statements(w, "wh1", ["CREATE CATALOG IF NOT EXISTS quest_data"])
    assert w.statement_execution.execute_statement.called


def test_run_uc_statements_sets_no_session_catalog():
    # Pinning the session to a catalog the first statement is about to CREATE
    # would fail before it ran, so no catalog is sent. Every statement Quest
    # issues is fully qualified.
    w = MagicMock()
    deploy.run_uc_statements(w, "wh1", ["CREATE CATALOG IF NOT EXISTS quest_data"])
    kwargs = w.statement_execution.execute_statement.call_args.kwargs
    assert "catalog" not in kwargs


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
def test_lakebase_types_resolve_on_this_sdk():
    # The Lakebase dataclasses moved from service.catalog (sdk 0.54/0.55) to
    # service.database (0.56+). The helper must find them either way.
    instance_cls, state_cls = deploy.lakebase_types()
    assert instance_cls.__name__ == "DatabaseInstance"
    assert state_cls.AVAILABLE.value == "AVAILABLE"


def test_lakebase_api_prefers_current_name():
    w = MagicMock()
    assert deploy.lakebase_api(w) is w.database


def test_lakebase_api_falls_back_to_legacy_name():
    # An sdk 0.55 client has database_instances but no database attribute.
    class Legacy:
        database_instances = "legacy-api"

    assert deploy.lakebase_api(Legacy()) == "legacy-api"


def test_lakebase_api_raises_when_absent():
    import pytest

    class Neither:
        pass

    with pytest.raises(RuntimeError, match="no Lakebase API"):
        deploy.lakebase_api(Neither())


def test_provision_lakebase_creates_instance_when_missing():
    # Readiness signal is state == AVAILABLE with a read_write_dns host.
    w = MagicMock()
    inst = MagicMock()
    inst.read_write_dns = "ep-x.database.cloud.databricks.com"
    inst.state = "AVAILABLE"
    w.database.create_database_instance.return_value = inst
    # miss (not found) on first get, then hit on the poll
    w.database.get_database_instance.side_effect = [Exception("nf"), inst]
    out = deploy.provision_lakebase(w, "databricks-quest")
    assert w.database.create_database_instance.called
    assert out["host"] == "ep-x.database.cloud.databricks.com"


def test_provision_lakebase_reuses_existing_instance():
    # Re-running the deployer must not create a second instance.
    w = MagicMock()
    inst = MagicMock()
    inst.read_write_dns = "ep-x.database.cloud.databricks.com"
    inst.state = "AVAILABLE"
    w.database.get_database_instance.return_value = inst
    out = deploy.provision_lakebase(w, "databricks-quest")
    w.database.create_database_instance.assert_not_called()
    assert out["host"] == "ep-x.database.cloud.databricks.com"


def test_lakebase_credential_uses_sdk_method_when_present():
    w = MagicMock()
    w.database.generate_database_credential.return_value = MagicMock(token="tok-123")
    assert deploy._lakebase_credential(w, "databricks-quest") == "tok-123"


def test_lakebase_credential_falls_back_to_rest():
    # Older SDKs lack the method; the REST endpoint the sync notebook uses is the
    # fallback and takes the instance name as a project endpoint path.
    class LegacyDb:
        pass

    w = MagicMock()
    w.database = LegacyDb()
    w.api_client.do.return_value = {"token": "tok-rest"}
    assert deploy._lakebase_credential(w, "databricks-quest") == "tok-rest"
    args, kwargs = w.api_client.do.call_args
    assert args[1] == "/api/2.0/postgres/credentials"
    assert "projects/databricks-quest" in kwargs["body"]["endpoint"]


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


# ── Scoring job: full task graph + idempotency ────────────────────────────────
def _graph():
    return deploy.scoring_task_graph(
        "/Workspace/Users/me/databricks-quest/notebooks",
        "quest_data", "quest", "wh1", "databricks-quest",
        "lakebase", "ep-x.database.cloud.databricks.com", "quest_db",
    )


def test_task_graph_covers_all_four_notebooks():
    # Scoring alone leaves Lakebase empty: the sync task is what moves scored
    # Delta rows into the database the app reads.
    keys = [t["task_key"] for t in _graph()]
    assert keys == [
        "roundtrip_attestations",
        "run_scoring",
        "sync_to_lakebase",
        "warm_warehouse",
    ]
    paths = [t["notebook_path"].rsplit("/", 1)[-1] for t in _graph()]
    assert paths == [
        "roundtrip_attestations",
        "scoring_pipeline",
        "lakebase_sync",
        "warm_warehouse",
    ]


def test_task_graph_ordering_matches_bundle():
    # Attestations land in Delta before scoring reads them; sync runs after
    # scoring so the app sees fresh numbers.
    by_key = {t["task_key"]: t for t in _graph()}
    assert by_key["roundtrip_attestations"]["depends_on"] == []
    assert by_key["run_scoring"]["depends_on"] == ["roundtrip_attestations"]
    assert by_key["sync_to_lakebase"]["depends_on"] == ["run_scoring"]
    assert by_key["warm_warehouse"]["depends_on"] == ["run_scoring"]


def test_task_graph_passes_lakebase_params_to_sync():
    by_key = {t["task_key"]: t for t in _graph()}
    p = by_key["sync_to_lakebase"]["params"]
    assert p["lakebase_host"] == "ep-x.database.cloud.databricks.com"
    assert p["lakebase_db"] == "quest_db"
    assert p["quest_catalog"] == "quest_data"


def test_job_declares_psycopg2_for_serverless():
    # lakebase_sync and roundtrip_attestations import psycopg2, which serverless
    # does not preinstall.
    envs = deploy._job_environments()
    assert envs[0].environment_key == deploy.ENVIRONMENT_KEY
    assert "psycopg2-binary" in envs[0].spec.dependencies


def _existing_job(w, job_id=4242, bundle=False):
    existing = MagicMock()
    existing.job_id = job_id
    w.jobs.list.return_value = iter([existing])
    kind = MagicMock()
    kind.value = "BUNDLE" if bundle else "OTHER"
    w.jobs.get.return_value.settings.deployment = kind if bundle else None
    if bundle:
        w.jobs.get.return_value.settings.deployment.kind = kind
    return existing


def test_scheduled_job_updates_existing_instead_of_duplicating():
    w = MagicMock()
    _existing_job(w)
    job_id = deploy.create_scheduled_job(w, _graph(), "databricks-quest")
    assert job_id == 4242
    w.jobs.create.assert_not_called()
    w.jobs.update.assert_called_once()
    assert w.jobs.update.call_args.kwargs["job_id"] == 4242


def test_scheduled_job_updates_rather_than_resets():
    # reset() replaces the entire settings object and would drop tags,
    # notifications, or timeouts a user added by hand.
    w = MagicMock()
    _existing_job(w)
    deploy.create_scheduled_job(w, _graph(), "databricks-quest")
    w.jobs.reset.assert_not_called()


def test_scheduled_job_leaves_bundle_managed_job_alone():
    # deploy.sh deploys via DAB, and at the default app name the bundle's job has
    # exactly this name. Rewriting it would desync bundle state.
    w = MagicMock()
    _existing_job(w, job_id=99, bundle=True)
    assert deploy.create_scheduled_job(w, _graph(), "databricks-quest") == 99
    w.jobs.update.assert_not_called()
    w.jobs.reset.assert_not_called()
    w.jobs.create.assert_not_called()


def test_scheduled_job_creates_when_absent():
    w = MagicMock()
    w.jobs.list.return_value = iter([])
    w.jobs.create.return_value = MagicMock(job_id=77)
    assert deploy.create_scheduled_job(w, _graph(), "databricks-quest") == 77
    kwargs = w.jobs.create.call_args.kwargs
    assert kwargs["max_concurrent_runs"] == 1
    assert kwargs["schedule"].quartz_cron_expression == "0 0 */4 * * ?"
    assert kwargs["schedule"].timezone_id == "UTC"
    assert len(kwargs["tasks"]) == 4


def test_job_name_is_scoped_to_the_app():
    # Two Quest deployments in one workspace must not share a job: the lookup is
    # by name, so a fixed name would make the second deploy overwrite the first.
    assert deploy.job_name("quest-a") != deploy.job_name("quest-b")
    assert "quest-a" in deploy.job_name("quest-a")


def test_scheduled_job_looks_up_by_scoped_name():
    w = MagicMock()
    w.jobs.list.return_value = iter([])
    w.jobs.create.return_value = MagicMock(job_id=1)
    deploy.create_scheduled_job(w, _graph(), "quest-alpha")
    assert w.jobs.list.call_args.kwargs["name"] == deploy.job_name("quest-alpha")
    assert w.jobs.create.call_args.kwargs["name"] == deploy.job_name("quest-alpha")


def test_scheduled_job_enables_queueing():
    # max_concurrent_runs=1 plus queueing: an overlapping trigger waits instead
    # of being rejected.
    w = MagicMock()
    w.jobs.list.return_value = iter([])
    w.jobs.create.return_value = MagicMock(job_id=77)
    deploy.create_scheduled_job(w, _graph(), "databricks-quest")
    assert w.jobs.create.call_args.kwargs["queue"].enabled is True


def test_trigger_scoring_runs_the_job_not_a_detached_run():
    # A detached jobs.submit would bypass max_concurrent_runs and could collide
    # with the 4-hourly schedule on a Delta ConcurrentDeleteReadException.
    w = MagicMock()
    deploy.trigger_scoring(w, 4242)
    w.jobs.run_now.assert_called_once_with(job_id=4242)
    w.jobs.submit.assert_not_called()


# ── Windows-safe console output ───────────────────────────────────────────────
def test_glyphs_fall_back_to_ascii_on_legacy_windows_encoding(monkeypatch):
    # Redirected stdout on Windows uses cp1252, which cannot encode the check
    # mark; printing it there raises UnicodeEncodeError.
    class FakeStdout:
        encoding = "cp1252"

    monkeypatch.setattr(deploy.sys, "stdout", FakeStdout())
    glyphs = deploy._pick_glyphs()
    assert glyphs["ok"] == "[OK]"
    for value in glyphs.values():
        value.encode("cp1252")  # must not raise


def test_glyphs_stay_unicode_on_utf8(monkeypatch):
    class FakeStdout:
        encoding = "utf-8"

    monkeypatch.setattr(deploy.sys, "stdout", FakeStdout())
    assert deploy._pick_glyphs()["ok"] == "\u2713"


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


# ── Workspace ID scope: parsing, validation, resolution ──────────────────────
def test_normalize_workspace_ids_all_lowercase():
    assert deploy.normalize_workspace_ids("all") == "all"


def test_normalize_workspace_ids_all_uppercase():
    assert deploy.normalize_workspace_ids("ALL") == "all"


def test_normalize_workspace_ids_all_mixed_case():
    assert deploy.normalize_workspace_ids("All") == "all"


def test_normalize_workspace_ids_single_id():
    assert deploy.normalize_workspace_ids("1234567890123456") == "1234567890123456"


def test_normalize_workspace_ids_multiple_ids():
    assert deploy.normalize_workspace_ids("111,222,333") == "111,222,333"


def test_normalize_workspace_ids_whitespace_separated():
    assert deploy.normalize_workspace_ids("111 222 333") == "111,222,333"


def test_normalize_workspace_ids_mixed_whitespace_and_commas():
    assert deploy.normalize_workspace_ids(" 111 , 222 \n 333 ") == "111,222,333"


def test_normalize_workspace_ids_drops_duplicates():
    assert deploy.normalize_workspace_ids("111,111,222") == "111,222"


def test_normalize_workspace_ids_keeps_first_occurrence():
    assert deploy.normalize_workspace_ids("111,222,111,222") == "111,222"


def test_normalize_workspace_ids_rejects_empty():
    import pytest
    with pytest.raises(ValueError, match="no workspace IDs given"):
        deploy.normalize_workspace_ids("")


def test_normalize_workspace_ids_rejects_whitespace_only():
    import pytest
    with pytest.raises(ValueError, match="no workspace IDs given"):
        deploy.normalize_workspace_ids("   ")


def test_normalize_workspace_ids_rejects_all_mixed_with_ids():
    import pytest
    with pytest.raises(ValueError, match="can't be combined"):
        deploy.normalize_workspace_ids("all,111")


def test_normalize_workspace_ids_rejects_ids_mixed_with_all():
    import pytest
    with pytest.raises(ValueError, match="can't be combined"):
        deploy.normalize_workspace_ids("111,all")


def test_normalize_workspace_ids_rejects_zero():
    import pytest
    with pytest.raises(ValueError, match="invalid workspace ID"):
        deploy.normalize_workspace_ids("0")


def test_normalize_workspace_ids_rejects_leading_zero():
    import pytest
    with pytest.raises(ValueError, match="invalid workspace ID"):
        deploy.normalize_workspace_ids("0123")


def test_normalize_workspace_ids_rejects_non_digit():
    import pytest
    with pytest.raises(ValueError, match="invalid workspace ID"):
        deploy.normalize_workspace_ids("12a")


def test_normalize_workspace_ids_rejects_negative():
    import pytest
    with pytest.raises(ValueError, match="invalid workspace ID"):
        deploy.normalize_workspace_ids("-5")


def test_normalize_workspace_ids_rejects_decimal():
    import pytest
    with pytest.raises(ValueError, match="invalid workspace ID"):
        deploy.normalize_workspace_ids("1.5")


def test_normalize_workspace_ids_rejects_20_digits():
    import pytest
    with pytest.raises(ValueError, match="invalid workspace ID"):
        deploy.normalize_workspace_ids("12345678901234567890")


def test_normalize_workspace_ids_accepts_19_digits():
    # 19 digits is the max: BIGINT's 2^63-1 is 19 digits long
    result = deploy.normalize_workspace_ids("1234567890123456789")
    assert result == "1234567890123456789"


def test_parser_workspace_ids_default():
    args = deploy.build_parser().parse_args(["--catalog", "quest_data"])
    assert args.workspace_ids is None


def test_parser_workspace_ids_value():
    args = deploy.build_parser().parse_args(
        ["--catalog", "quest_data", "--workspace-ids", "111,222"]
    )
    assert args.workspace_ids == "111,222"


def test_config_workspace_ids_from_args():
    args = deploy.build_parser().parse_args(
        ["--catalog", "quest_data", "--workspace-ids", "111"]
    )
    cfg = deploy._config_from_args(args)
    assert cfg.workspace_ids == "111"


def test_current_scoring_workspace_ids_not_found():
    w = MagicMock()
    w.jobs.list.return_value = iter([])
    result = deploy.current_scoring_workspace_ids(w, "databricks-quest")
    assert result is None


def test_current_scoring_workspace_ids_found():
    w = MagicMock()
    existing = MagicMock()
    existing.job_id = 4242
    w.jobs.list.return_value = iter([existing])

    job = MagicMock()
    task = MagicMock()
    task.task_key = "run_scoring"
    task.notebook_task.base_parameters = {"workspace_ids": "111,222"}
    job.settings.tasks = [task]
    w.jobs.get.return_value = job

    result = deploy.current_scoring_workspace_ids(w, "databricks-quest")
    assert result == "111,222"


def test_current_scoring_workspace_ids_task_not_found():
    w = MagicMock()
    existing = MagicMock()
    existing.job_id = 4242
    w.jobs.list.return_value = iter([existing])

    job = MagicMock()
    task = MagicMock()
    task.task_key = "other_task"
    job.settings.tasks = [task]
    w.jobs.get.return_value = job

    result = deploy.current_scoring_workspace_ids(w, "databricks-quest")
    assert result is None


def test_current_scoring_workspace_ids_none_at_notebook_task():
    w = MagicMock()
    existing = MagicMock()
    existing.job_id = 4242
    w.jobs.list.return_value = iter([existing])

    job = MagicMock()
    task = MagicMock()
    task.task_key = "run_scoring"
    task.notebook_task = None
    job.settings.tasks = [task]
    w.jobs.get.return_value = job

    result = deploy.current_scoring_workspace_ids(w, "databricks-quest")
    assert result is None


def test_current_scoring_workspace_ids_none_at_base_parameters():
    w = MagicMock()
    existing = MagicMock()
    existing.job_id = 4242
    w.jobs.list.return_value = iter([existing])

    job = MagicMock()
    task = MagicMock()
    task.task_key = "run_scoring"
    task.notebook_task.base_parameters = None
    job.settings.tasks = [task]
    w.jobs.get.return_value = job

    result = deploy.current_scoring_workspace_ids(w, "databricks-quest")
    assert result is None


def test_scoring_task_graph_default_workspace_ids():
    graph = deploy.scoring_task_graph(
        "/Workspace/Users/me/databricks-quest/notebooks",
        "quest_data", "quest", "wh1", "databricks-quest",
        "lakebase", "ep-x.database.cloud.databricks.com", "quest_db",
    )
    by_key = {t["task_key"]: t for t in graph}
    assert by_key["run_scoring"]["params"]["workspace_ids"] == "all"


def test_scoring_task_graph_explicit_workspace_ids():
    graph = deploy.scoring_task_graph(
        "/Workspace/Users/me/databricks-quest/notebooks",
        "quest_data", "quest", "wh1", "databricks-quest",
        "lakebase", "ep-x.database.cloud.databricks.com", "quest_db",
        workspace_ids="111,222",
    )
    by_key = {t["task_key"]: t for t in graph}
    assert by_key["run_scoring"]["params"]["workspace_ids"] == "111,222"


def test_scoring_task_graph_other_tasks_unchanged():
    graph = deploy.scoring_task_graph(
        "/Workspace/Users/me/databricks-quest/notebooks",
        "quest_data", "quest", "wh1", "databricks-quest",
        "lakebase", "ep-x.database.cloud.databricks.com", "quest_db",
        workspace_ids="111,222",
    )
    by_key = {t["task_key"]: t for t in graph}
    # workspace_ids should not be in other tasks
    assert "workspace_ids" not in by_key["roundtrip_attestations"]["params"]
    assert "workspace_ids" not in by_key["sync_to_lakebase"]["params"]
    assert "workspace_ids" not in by_key["warm_warehouse"]["params"]


def test_app_yaml_scoring_workspace_ids_not_emitted_when_all():
    y = deploy.render_app_yaml(
        backend="warehouse",
        catalog="quest_data",
        schema="quest",
        warehouse_id="wh123",
        admins="",
        scoring_workspace_ids="all",
    )
    assert "QUEST_SCORING_WORKSPACE_IDS" not in y


def test_app_yaml_scoring_workspace_ids_emitted_when_scoped():
    y = deploy.render_app_yaml(
        backend="warehouse",
        catalog="quest_data",
        schema="quest",
        warehouse_id="wh123",
        admins="",
        scoring_workspace_ids="111,222",
    )
    assert "QUEST_SCORING_WORKSPACE_IDS" in y
    assert "111,222" in y


def test_check_scoring_workspaces_all_never_checks():
    w = MagicMock()
    deploy.check_scoring_workspaces(w, "wh1", "all")
    w.statement_execution.execute_statement.assert_not_called()


def test_check_scoring_workspaces_statement_raises_logs_warning(capsys, monkeypatch):
    w = MagicMock()
    w.statement_execution.execute_statement.side_effect = Exception("permission denied")
    deploy.check_scoring_workspaces(w, "wh1", "111")
    out = capsys.readouterr().out
    assert "couldn't check" in out.lower()


def test_check_scoring_workspaces_statement_timeout_logs_warning(capsys):
    w = MagicMock()
    resp = MagicMock()
    resp.statement_id = "stmt-123"
    resp.status.state.value = "RUNNING"  # never reaches a terminal state
    w.statement_execution.execute_statement.return_value = resp
    w.statement_execution.get_statement.return_value = resp

    deploy.check_scoring_workspaces(w, "wh1", "111", timeout_s=0.0, poll_s=0.0)
    out = capsys.readouterr().out
    assert "timed out" in out.lower()
    # A statement we stop waiting for is cancelled, not left running on the warehouse.
    w.statement_execution.cancel_execution.assert_called_once_with(statement_id="stmt-123")


def _succeeded(rows):
    resp = MagicMock()
    resp.status.state.value = "SUCCEEDED"
    resp.result.data_array = rows
    return resp


def test_check_scoring_workspaces_waits_inline_at_most_50s():
    # The Statement Execution API rejects an inline wait above 50s.
    w = MagicMock()
    w.statement_execution.execute_statement.return_value = _succeeded([])
    deploy.check_scoring_workspaces(w, "wh1", "111")
    kwargs = w.statement_execution.execute_statement.call_args.kwargs
    assert kwargs["wait_timeout"] == "50s"
    assert kwargs["warehouse_id"] == "wh1"


def test_check_scoring_workspaces_aggregates_each_table_before_joining():
    sql = deploy._workspace_check_sql(["111", "222"], with_names=True)
    # billing and audit are each reduced to one row per workspace, so the join
    # never multiplies raw billing rows by raw audit rows.
    assert sql.count("GROUP BY workspace_id") == 2
    assert "workspace_id IN ('111', '222')" in sql
    # "the last 45 days" / "the last 7 days", today included (not 46 / 8 calendar days).
    assert "usage_date > current_date() - 45" in sql
    assert "event_date > current_date() - 7" in sql
    assert "system.access.workspaces_latest" in sql
    assert "system.access.workspaces_latest" not in deploy._workspace_check_sql(["111"], with_names=False)


def test_check_scoring_workspaces_reports_each_id(capsys):
    # Statement results arrive as strings; status values are RUNNING/PROVISIONING/...
    rows = [
        ["111", "Finance Prod", "RUNNING", "30", "5400"],   # healthy
        ["222", "Finance Dev", "RUNNING", "0", "0"],        # no billing
        ["333", "Sydney", "RUNNING", "12", "0"],            # billing but no audit here
        ["444", None, None, "0", "0"],                      # not in workspaces_latest
        ["555", "New one", "PROVISIONING", "3", "10"],      # not running yet
    ]
    w = MagicMock()
    w.statement_execution.execute_statement.return_value = _succeeded(rows)
    deploy.check_scoring_workspaces(w, "wh1", "111,222,333,444,555")
    out = capsys.readouterr().out
    assert "111 (Finance Prod): active (billing on 30 of the last 45 days)" in out
    assert "222 (Finance Dev): no billing activity in the last 45 days" in out
    assert "333 (Sydney): billing activity but no audit rows here" in out
    assert "probably in another cloud region" in out
    assert "only billing and Unity Catalog missions can score it" in out
    assert "444: not found in this account" in out
    assert "555 (New one): status is PROVISIONING" in out
    assert "111 (Finance Prod): status" not in out


def test_check_scoring_workspaces_falls_back_without_names(capsys):
    # system.access.workspaces_latest is Public Preview; if it fails, retry without it.
    failed = MagicMock()
    failed.status.state.value = "FAILED"
    failed.status.error.message = "TABLE_OR_VIEW_NOT_FOUND: workspaces_latest"
    w = MagicMock()
    w.statement_execution.execute_statement.side_effect = [
        failed,
        _succeeded([["111", None, None, "4", "0"]]),
    ]
    deploy.check_scoring_workspaces(w, "wh1", "111")
    out = capsys.readouterr().out
    second_sql = w.statement_execution.execute_statement.call_args_list[1].kwargs["statement"]
    assert "workspaces_latest" not in second_sql
    assert "not found in this account" not in out  # unknown without the names table
    assert "probably in another cloud region" in out


def test_check_scoring_workspaces_both_attempts_failing_warns(capsys):
    w = MagicMock()
    w.statement_execution.execute_statement.side_effect = Exception("PERMISSION_DENIED: system.billing")
    deploy.check_scoring_workspaces(w, "wh1", "111")
    out = capsys.readouterr().out
    assert "couldn't check the workspace ids" in out.lower()
    assert "PERMISSION_DENIED" in out
    assert w.statement_execution.execute_statement.call_count == 2


# ── resolve_scoring_scope: flag vs the scoring job's current value ────────────
def _fake_job(value=None, bundle=False, has_param=True):
    task = MagicMock()
    task.task_key = "run_scoring"
    task.notebook_task.base_parameters = {"workspace_ids": value} if has_param else {"quest_catalog": "c"}
    job = MagicMock()
    job.settings.tasks = [task]
    job.settings.deployment = MagicMock(kind="BUNDLE") if bundle else None
    return job


def _with_job(monkeypatch, job=None, raises=None):
    def fake(w, app_name):
        if raises:
            raise raises
        return job
    monkeypatch.setattr(deploy, "_find_scoring_job", fake)


def test_scope_flag_wins_and_warns_on_change(monkeypatch, capsys):
    _with_job(monkeypatch, _fake_job("111"))
    d = deploy.resolve_scoring_scope(MagicMock(), "app", "222")
    assert (d.scope, d.check_last_run, d.bundle_managed) == ("222", False, False)
    assert "changes from 111 to 222" in capsys.readouterr().out


def test_scope_omitted_keeps_current(monkeypatch, capsys):
    _with_job(monkeypatch, _fake_job("111,222"))
    d = deploy.resolve_scoring_scope(MagicMock(), "app", None)
    assert (d.scope, d.check_last_run) == ("111,222", False)
    assert "Keeping the current scoring scope" in capsys.readouterr().out


def test_scope_omitted_with_no_usable_value_checks_the_last_run(monkeypatch):
    # No job yet, a job from before this setting (no parameter), or an empty value
    # (the bundle default) all mean "all", but only after confirming the last run
    # wasn't scoped (see test_run_* below).
    for job in (None, _fake_job(has_param=False), _fake_job(""), _fake_job("  ")):
        _with_job(monkeypatch, job)
        d = deploy.resolve_scoring_scope(MagicMock(), "app", None)
        assert (d.scope, d.check_last_run) == ("all", True)


def test_scope_omitted_and_unreadable_stops_the_deploy(monkeypatch, capsys):
    _with_job(monkeypatch, raises=Exception("PERMISSION_DENIED"))
    assert deploy.resolve_scoring_scope(MagicMock(), "app", None) is None
    assert "Pass --workspace-ids explicitly" in capsys.readouterr().out


def test_scope_omitted_and_malformed_stops_the_deploy(monkeypatch, capsys):
    _with_job(monkeypatch, _fake_job("abc"))
    assert deploy.resolve_scoring_scope(MagicMock(), "app", None) is None
    assert "isn't valid" in capsys.readouterr().out


def test_scope_flag_given_ignores_unreadable_or_malformed_current(monkeypatch):
    _with_job(monkeypatch, raises=Exception("boom"))
    assert deploy.resolve_scoring_scope(MagicMock(), "app", "all").scope == "all"
    _with_job(monkeypatch, _fake_job("abc"))
    assert deploy.resolve_scoring_scope(MagicMock(), "app", "111").scope == "111"


def test_scope_on_a_bundle_job_is_left_to_the_bundle(monkeypatch, capsys):
    # deploy.py never edits a bundle-managed job, so it must not claim a new scope.
    _with_job(monkeypatch, _fake_job("", bundle=True))
    assert deploy.resolve_scoring_scope(MagicMock(), "app", "111") is None
    assert "managed by a Databricks Asset Bundle" in capsys.readouterr().out
    d = deploy.resolve_scoring_scope(MagicMock(), "app", None)
    assert (d.scope, d.check_last_run, d.bundle_managed) == ("all", False, True)
    _with_job(monkeypatch, _fake_job("111", bundle=True))
    d = deploy.resolve_scoring_scope(MagicMock(), "app", "111")  # same value: fine
    assert (d.scope, d.bundle_managed) == ("111", True)


# ── last_scoring_scope: the record the scoring notebook writes ────────────────
def _stmt(state, rows=None, error=None):
    resp = MagicMock()
    resp.status.state.value = state
    resp.status.error.message = error
    resp.result.data_array = rows
    return resp


def test_last_scoring_scope_reads_the_record():
    w = MagicMock()
    w.statement_execution.execute_statement.return_value = _stmt("SUCCEEDED", [["111,222"]])
    assert deploy.last_scoring_scope(w, "wh", "cat", "sch") == "111,222"
    sql = w.statement_execution.execute_statement.call_args.kwargs["statement"]
    assert "`cat`.`sch`.app_settings" in sql and "scoring_scope_last_run" in sql
    w.statement_execution.execute_statement.return_value = _stmt("SUCCEEDED", [[None]])
    assert deploy.last_scoring_scope(w, "wh", "cat", "sch") == ""


def test_last_scoring_scope_none_when_there_is_no_record_yet():
    w = MagicMock()
    w.statement_execution.execute_statement.return_value = _stmt("SUCCEEDED", [])
    assert deploy.last_scoring_scope(w, "wh", "cat", "sch") is None
    w.statement_execution.execute_statement.return_value = _stmt(
        "FAILED", error="[TABLE_OR_VIEW_NOT_FOUND] The table or view `cat`.`sch`.`app_settings` cannot be found."
    )
    assert deploy.last_scoring_scope(w, "wh", "cat", "sch") is None


def test_last_scoring_scope_raises_when_unreadable():
    w = MagicMock()
    w.statement_execution.execute_statement.return_value = _stmt("FAILED", error="PERMISSION_DENIED: no SELECT")
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        deploy.last_scoring_scope(w, "wh", "cat", "sch")


# ── run(): the last-run check before writing 'all' ────────────────────────────
class _ReachedUpload(Exception):
    """Raised by the stubbed upload step: the deploy got past the scope checks."""


def _stub_run_until_upload(monkeypatch, last_scope):
    w = MagicMock()
    w.current_user.me.return_value.user_name = "me@x.com"
    monkeypatch.setattr(deploy, "get_client", lambda *a, **k: w)
    monkeypatch.setattr(deploy, "resolve_scoring_scope",
                        lambda *a, **k: deploy.ScopeDecision("all", check_last_run=True))
    monkeypatch.setattr(deploy, "resolve_warehouse", lambda *a, **k: "wh")
    monkeypatch.setattr(deploy, "resolve_catalog", lambda *a, **k: "cat")
    monkeypatch.setattr(deploy, "setup_unity_catalog", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "last_scoring_scope", lambda *a, **k: last_scope)

    def reached(*a, **k):
        raise _ReachedUpload()
    monkeypatch.setattr(deploy, "upload_dir", reached)
    return deploy.Config(catalog="cat", non_interactive=True)


def test_run_stops_instead_of_widening_a_scoped_deployment(monkeypatch, capsys):
    # The job lost its workspace_ids (e.g. recreated by an older deploy.py), but the
    # last scoring run was scoped: writing 'all' would widen it for good.
    cfg = _stub_run_until_upload(monkeypatch, last_scope="111,222")
    assert deploy.run(cfg) == 1
    out = capsys.readouterr().out
    assert "its last run scored only workspaces 111,222" in out
    assert "--workspace-ids 111,222" in out


def test_run_continues_when_the_last_run_was_unscoped_or_never_ran(monkeypatch):
    for last in ("", None):
        cfg = _stub_run_until_upload(monkeypatch, last_scope=last)
        with pytest.raises(_ReachedUpload):
            deploy.run(cfg)
