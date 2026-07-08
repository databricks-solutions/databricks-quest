"""Resource bootstrap & reset (PR08).

The namespace guard and plan builders are pure, so they're tested directly.
Endpoints compose repos + an injected executor; we stub the repo singletons and
a fake executor and call the async handlers directly (FastAPI ``Depends`` is a
no-op in-process).
"""

import asyncio

import pytest
from fastapi import HTTPException

from services import namespace as ns
from services import resource_service as rsvc


@pytest.fixture
def main_module():
    import main

    return main


def _run(coro):
    return asyncio.run(coro)


EVENT = {"event_id": "evt_1", "slug": "ai-bi-day"}
TEAMS = [
    {"team_id": "team_red", "name": "Red Team"},
    {"team_id": "team_blue", "name": "Blue", "team_catalog": "custom_cat", "team_schema": "blue_sandbox"},
]


# ── Namespace + identifier guards ────────────────────────────────────────────


def test_event_namespace_defaults_from_slug():
    out = ns.event_namespace(EVENT)
    assert out["catalog"] == "quest_ai_bi_day"
    assert out["schema_prefix"] == "team_"


def test_event_namespace_honours_config():
    ev = {"event_id": "e", "slug": "s", "config_json": {"resource_namespace": {"catalog": "gameday_q3", "schema_prefix": "grp_"}}}
    out = ns.event_namespace(ev)
    assert out == {"catalog": "gameday_q3", "schema_prefix": "grp_"}


def test_event_namespace_rejects_reserved_catalog():
    ev = {"event_id": "e", "slug": "s", "config_json": {"resource_namespace": {"catalog": "main"}}}
    with pytest.raises(ns.NamespaceError) as exc:
        ns.event_namespace(ev)
    assert exc.value.code == "RESERVED_CATALOG"


@pytest.mark.parametrize("bad", ["", "has space", "a.b", "drop;", "wild*", 'q"x', "schema-1"])
def test_assert_valid_identifier_rejects_unsafe(bad):
    with pytest.raises(ns.NamespaceError):
        ns.assert_valid_identifier(bad)


def test_team_target_derives_and_honours_explicit():
    red = ns.team_target(EVENT, TEAMS[0])
    assert red["fqn"] == "quest_ai_bi_day.team_red_team"
    blue = ns.team_target(EVENT, TEAMS[1])
    assert blue["fqn"] == "custom_cat.blue_sandbox"


# ── assert_within_namespace (the destructive-action gate) ────────────────────


def test_within_namespace_allows_team_target():
    ns.assert_within_namespace("quest_ai_bi_day.team_red_team", EVENT, TEAMS)  # no raise


def test_within_namespace_blocks_foreign_schema():
    with pytest.raises(ns.NamespaceError) as exc:
        ns.assert_within_namespace("prod.customer_pii", EVENT, TEAMS)
    assert exc.value.code in ("OUTSIDE_NAMESPACE", "RESERVED_CATALOG")


def test_within_namespace_blocks_reserved_catalog():
    with pytest.raises(ns.NamespaceError) as exc:
        ns.assert_within_namespace("main.default", EVENT, TEAMS)
    assert exc.value.code == "RESERVED_CATALOG"


def test_within_namespace_requires_catalog_dot_schema():
    with pytest.raises(ns.NamespaceError) as exc:
        ns.assert_within_namespace("quest_ai_bi_day", EVENT, TEAMS)
    assert exc.value.code == "NOT_A_SCHEMA"


# ── Seed SQL templating ──────────────────────────────────────────────────────


def test_render_seed_sql_resolves_allowed_slots():
    target = ns.team_target(EVENT, TEAMS[0])
    out = ns.render_seed_sql(
        "CREATE TABLE ${team_catalog}.${team_schema}.t (id INT)", target, "evt_1"
    )
    assert out == "CREATE TABLE quest_ai_bi_day.team_red_team.t (id INT)"


def test_render_seed_sql_rejects_unknown_slot():
    target = ns.team_target(EVENT, TEAMS[0])
    with pytest.raises(ns.NamespaceError) as exc:
        ns.render_seed_sql("SELECT ${secret}", target, "evt_1")
    assert exc.value.code == "BAD_SEED_SLOT"


# ── Plan builders ────────────────────────────────────────────────────────────


def test_bootstrap_plan_creates_catalog_once_and_schema_per_team():
    resources = {"seed_sql": ["CREATE TABLE ${team_catalog}.${team_schema}.warmup (id INT)"]}
    plan = rsvc.build_bootstrap_plan(EVENT, TEAMS, resources)
    ops = [(i["op"], i["target"]) for i in plan]
    # Red team's catalog is the shared event catalog; Blue's is custom — 2 catalogs.
    assert ("create_catalog", "quest_ai_bi_day") in ops
    assert ("create_catalog", "custom_cat") in ops
    assert ("create_schema", "quest_ai_bi_day.team_red_team") in ops
    assert ("create_schema", "custom_cat.blue_sandbox") in ops
    # A seed statement per team, rendered to its schema.
    seeds = [i for i in plan if i["op"] == "seed"]
    assert len(seeds) == 2
    assert "quest_ai_bi_day.team_red_team.warmup" in seeds[0]["sql"]
    assert all(i["within_namespace"] for i in plan)


def test_reset_plan_drops_each_team_schema_within_namespace():
    plan = rsvc.build_reset_plan(EVENT, TEAMS)
    assert all(i["op"] == "drop_schema" and i["within_namespace"] for i in plan)
    assert {i["target"] for i in plan} == {"quest_ai_bi_day.team_red_team", "custom_cat.blue_sandbox"}
    assert "DROP SCHEMA IF EXISTS quest_ai_bi_day.team_red_team CASCADE" in [i["sql"] for i in plan]


# ── execute_plan ─────────────────────────────────────────────────────────────


class _FakeExecutor:
    def __init__(self, fail_on=None):
        self.run = []
        self.fail_on = fail_on or set()

    def __call__(self, sql, timeout):
        self.run.append(sql)
        if any(token in sql for token in self.fail_on):
            raise RuntimeError("boom")
        return []


def test_execute_plan_records_health_and_runs_all(monkeypatch):
    class _Repo:
        def __init__(self):
            self.calls = []

        def upsert(self, **kw):
            self.calls.append(kw)

    repo = _Repo()
    svc = rsvc.ResourceService(repo=repo)
    plan = rsvc.build_bootstrap_plan(EVENT, TEAMS, None)
    ex = _FakeExecutor()
    out = svc.execute_plan(EVENT, plan, ex, created_by="h@x")
    assert out["ok"] is True
    assert len(ex.run) == len(plan)
    # catalog + schema rows recorded as active
    assert any(c["fqn"] == "quest_ai_bi_day.team_red_team" and c["status"] == "active" for c in repo.calls)


def test_execute_plan_refuses_when_blockers_present():
    svc = rsvc.ResourceService(repo=object())
    plan = [{"op": "drop_schema", "target": "prod.pii", "sql": "DROP SCHEMA prod.pii CASCADE",
             "within_namespace": False, "resource_type": "schema"}]
    ex = _FakeExecutor()
    out = svc.execute_plan(EVENT, plan, ex)
    assert out["ok"] is False
    assert out["executed"] == []
    assert ex.run == []  # nothing ran


def test_execute_plan_marks_failed_statement(monkeypatch):
    class _Repo:
        def __init__(self):
            self.calls = []

        def upsert(self, **kw):
            self.calls.append(kw)

    repo = _Repo()
    svc = rsvc.ResourceService(repo=repo)
    plan = rsvc.build_bootstrap_plan(EVENT, [TEAMS[0]], None)
    ex = _FakeExecutor(fail_on={"CREATE SCHEMA"})
    out = svc.execute_plan(EVENT, plan, ex)
    assert out["ok"] is False
    assert any(c["status"] == "failed" for c in repo.calls)


# ── Endpoints ────────────────────────────────────────────────────────────────


def test_plan_endpoint_returns_blockers_empty_for_clean_event(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: EVENT)
    monkeypatch.setattr(m.events_repo, "list_teams", lambda e: TEAMS)
    monkeypatch.setattr(m.quest_packs_repo, "get_version", lambda pv: None)
    monkeypatch.setattr(m, "record_audit", lambda **kw: None)

    body = m.ResourcePlanPayload(action="bootstrap")
    out = _run(m.host_plan_resources("evt_1", body, user="h@x"))
    assert out["action"] == "bootstrap"
    assert out["blockers"] == []
    assert any(i["op"] == "create_schema" for i in out["plan"])


def test_reset_endpoint_requires_confirm(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: EVENT)
    monkeypatch.setattr(m.events_repo, "list_teams", lambda e: TEAMS)

    body = m.ResourceResetPayload(confirm=False)
    with pytest.raises(HTTPException) as exc:
        _run(m.host_reset_resources("evt_1", body, user="h@x"))
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "CONFIRM_REQUIRED"


def test_bootstrap_endpoint_503_without_warehouse(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: EVENT)
    monkeypatch.setattr(m.events_repo, "list_teams", lambda e: TEAMS)
    monkeypatch.setattr(m.quest_packs_repo, "get_version", lambda pv: None)
    monkeypatch.delenv("QUEST_SQL_WAREHOUSE_ID", raising=False)

    with pytest.raises(HTTPException) as exc:
        _run(m.host_bootstrap_resources("evt_1", user="h@x"))
    assert exc.value.status_code == 503
    assert exc.value.detail["error"]["code"] == "NO_WAREHOUSE"


def test_reset_endpoint_executes_with_injected_warehouse(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: EVENT)
    monkeypatch.setattr(m.events_repo, "list_teams", lambda e: TEAMS)
    monkeypatch.setattr(m, "record_audit", lambda **kw: None)

    ex = _FakeExecutor()
    monkeypatch.setattr(m, "_resource_executor", lambda: ex)

    captured = {}

    def fake_execute(ev, plan, executor, **kw):
        captured["plan"] = plan
        return {"ok": True, "executed": [], "blockers": []}

    monkeypatch.setattr(m.default_resource_service, "execute_plan", fake_execute)

    body = m.ResourceResetPayload(confirm=True)
    out = _run(m.host_reset_resources("evt_1", body, user="h@x"))
    assert out["action"] == "reset"
    assert out["ok"] is True
    # The plan handed to execute_plan drops only the two team schemas.
    assert {i["target"] for i in captured["plan"]} == {"quest_ai_bi_day.team_red_team", "custom_cat.blue_sandbox"}
