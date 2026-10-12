"""Workspace-scoped scoring: guards for notebooks/scoring_pipeline.py.

The deploy-time scope (``deploy.py --workspace-ids``) only works if every
system-table read in the scoring notebook goes through the scoped ``SYS_*``
sources. These tests keep it that way:

- no string in the notebook's code names a ``system.<schema>.<table>`` outside the
  ``_SYSTEM_TABLES`` map (the workspace-name lookup is the one allowed exception),
  so a new mission can't quietly read the whole account;
- the notebook's ``normalize_workspace_ids`` is in lockstep with deploy.py's;
- ``system_sources`` returns the bare table names when unscoped (so today's SQL
  runs unchanged) and the documented predicates when scoped;
- the widening guard runs before the first system-table read.

The notebook is valid Python (Databricks source format), so it is parsed with
``ast`` and the pieces under test are exec'd on their own, without Spark.
"""

import __future__
import ast
import importlib.util
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
NOTEBOOK = REPO / "notebooks" / "scoring_pipeline.py"
DEPLOY = REPO / "deploy.py"

SYSTEM_TABLE = re.compile(r"\bsystem\.[a-z_]+\.[a-z_]+")
SQL_COMMENT = re.compile(r"--[^\n]*")
NAME_LOOKUP = "system.access.workspaces_latest"

EXPECTED_TABLES = {
    "billing_usage": "system.billing.usage",
    "audit": "system.access.audit",
    "assistant_events": "system.access.assistant_events",
    "query_history": "system.query.history",
    "jobs": "system.lakeflow.jobs",
    "job_tasks": "system.lakeflow.job_tasks",
    "job_run_timeline": "system.lakeflow.job_run_timeline",
    "pipelines": "system.lakeflow.pipelines",
    "pipeline_update_timeline": "system.lakeflow.pipeline_update_timeline",
}


def _tree():
    return ast.parse(NOTEBOOK.read_text())


def _top_level_name(node):
    if isinstance(node, ast.FunctionDef):
        return node.name
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


def _notebook_ns(*names):
    """Exec the named top-level functions/assignments from the notebook, in source order."""
    picked = [n for n in _tree().body if _top_level_name(n) in names]
    missing = set(names) - {_top_level_name(n) for n in picked}
    assert not missing, f"scoring_pipeline.py no longer defines {sorted(missing)}"
    ns = {"re": re}
    module = ast.Module(body=picked, type_ignores=[])
    exec(compile(module, str(NOTEBOOK), "exec", flags=__future__.annotations.compiler_flag), ns)
    return ns


def _deploy_module():
    spec = importlib.util.spec_from_file_location("deploy_scope_lockstep", DEPLOY)
    module = importlib.util.module_from_spec(spec)
    # Register before exec_module so deploy.py's dataclass string annotations resolve
    # (same pattern as tests/test_deploy_py.py).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ── every system-table read goes through SYS_* ────────────────────────────────
def test_every_system_table_read_goes_through_the_scoped_sources():
    tree = _tree()
    table_map = next(
        (n.value for n in tree.body if _top_level_name(n) == "_SYSTEM_TABLES"), None
    )
    assert isinstance(table_map, ast.Dict), "_SYSTEM_TABLES must be a dict literal"
    allowed = {id(v) for v in table_map.values}

    offenders = []
    for node in ast.walk(tree):
        # String constants include docstrings and the literal parts of f-strings.
        # Python comments and `# MAGIC` markdown are not strings, so they're skipped.
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in allowed:
            continue
        for hit in SYSTEM_TABLE.findall(SQL_COMMENT.sub("", node.value)):
            if hit != NAME_LOOKUP:
                offenders.append(f"line {node.lineno}: {hit}")

    assert not offenders, (
        "Read system tables through the scoped SYS_* sources (e.g. FROM {SYS_BILLING_USAGE}), "
        "not by name, so the deploy-time workspace scope applies:\n  " + "\n  ".join(offenders)
    )


def test_system_tables_map_covers_the_nine_tables():
    assert _notebook_ns("_SYSTEM_TABLES")["_SYSTEM_TABLES"] == EXPECTED_TABLES


# ── lockstep with deploy.py ────────────────────────────────────────────────────
LOCKSTEP_CASES = [
    "111,222", " 111 , 222 ", "111 222\n333", "111,111,222",
    "all", "ALL", " All ",
    "", "  ", ",", "all,111",
    "0", "00", "0123", "12a", "-5", "1.5",
    "1234567890123456789",   # 19 digits: valid
    "12345678901234567890",  # 20 digits: invalid
]


def _outcome(fn, raw):
    try:
        return ("ok", fn(raw))
    except ValueError:
        return ("ValueError", None)


def test_normalize_workspace_ids_matches_deploy_py():
    notebook_fn = _notebook_ns("_WORKSPACE_ID_RE", "normalize_workspace_ids")["normalize_workspace_ids"]
    deploy_fn = _deploy_module().normalize_workspace_ids
    mismatches = [
        (raw, _outcome(notebook_fn, raw), _outcome(deploy_fn, raw))
        for raw in LOCKSTEP_CASES
        if _outcome(notebook_fn, raw) != _outcome(deploy_fn, raw)
    ]
    assert not mismatches, f"notebook vs deploy.py disagree: {mismatches}"


def test_normalize_workspace_ids_rules():
    fn = _notebook_ns("_WORKSPACE_ID_RE", "normalize_workspace_ids")["normalize_workspace_ids"]
    assert fn("111,222") == "111,222"
    assert fn(" 111 , 222 ") == "111,222"
    assert fn("111 222\n333") == "111,222,333"
    assert fn("111,111,222") == "111,222"
    assert fn("ALL") == "all"
    assert fn("1234567890123456789") == "1234567890123456789"
    for bad in ["", "  ", ",", "all,111", "0", "00", "0123", "12a", "-5", "1.5", "12345678901234567890"]:
        assert _outcome(fn, bad) == ("ValueError", None), f"{bad!r} should be rejected"


# ── scoped sources ─────────────────────────────────────────────────────────────
def _system_sources():
    return _notebook_ns("_SYSTEM_TABLES", "system_sources")["system_sources"]


def test_unscoped_sources_are_the_bare_table_names():
    # No scope must mean byte-for-byte the SQL that ran before this setting existed.
    assert _system_sources()(()) == EXPECTED_TABLES


def test_scoped_sources_filter_on_workspace_id():
    sources = _system_sources()(("111", "222"))
    for name, table in EXPECTED_TABLES.items():
        if name == "audit":
            continue
        assert sources[name] == f"(SELECT * FROM {table} WHERE workspace_id IN ('111', '222'))"


def test_scoped_audit_also_matches_account_level_events_by_origin():
    # Unity Catalog / lineage events are account-level: workspace_id '0', with the
    # originating workspace in request_params.workspace_id (audit log reference).
    assert _system_sources()(("111", "222"))["audit"] == (
        "(SELECT * FROM system.access.audit WHERE "
        "(workspace_id IN ('111', '222') OR "
        "(workspace_id = '0' AND request_params['workspace_id'] IN ('111', '222'))))"
    )


# ── widget + widening guard ────────────────────────────────────────────────────
def test_workspace_ids_widget_defaults_to_not_provided():
    assert re.search(r'dbutils\.widgets\.text\("workspace_ids",\s*""', NOTEBOOK.read_text())


def test_widening_guard_runs_before_the_first_system_table_read():
    tree = _tree()
    guard_lines = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "scoring_scope_last_run" in n.value
    ]
    first_read = min(
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.FormattedValue) and isinstance(n.value, ast.Name) and n.value.id.startswith("SYS_")
    )
    assert guard_lines, "the widening guard (app_settings key 'scoring_scope_last_run') is missing"
    assert min(guard_lines) < first_read, "the widening guard must run before the first system-table read"
    assert "This deployment scores only workspaces" in NOTEBOOK.read_text()
