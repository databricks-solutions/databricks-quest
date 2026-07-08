"""Security, observability & audit hardening (PR10).

Covers: request-id correlation + standard error envelope, expanded health
checks, structured validation/scoring logging, SQL-safety (destructive blocked,
template injection refused), and role enforcement.
"""

import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from validators import safety
from services import observability as obs


@pytest.fixture
def client():
    import main

    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def main_module():
    import main

    return main


# ── Request id + error envelope ──────────────────────────────────────────────


def test_health_returns_request_id_header(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.headers.get(obs.REQUEST_ID_HEADER)


def test_inbound_request_id_is_echoed(client):
    res = client.get("/api/health", headers={obs.REQUEST_ID_HEADER: "trace-abc-123"})
    assert res.headers.get(obs.REQUEST_ID_HEADER) == "trace-abc-123"


def test_error_envelope_has_request_id(client):
    # A host endpoint with event mode off raises HTTPException; our handler must
    # wrap it in {"error": {...request_id}} and stamp the response header.
    res = client.get("/api/host/events/evt_x/resources")
    assert res.status_code in (403, 404)
    body = res.json()
    assert "error" in body and "request_id" in body["error"]
    assert res.headers.get(obs.REQUEST_ID_HEADER)


def test_httpexception_envelope_preserves_code(client):
    # Host endpoints 404 with EVENT_MODE_DISABLED when event mode is off; the
    # handler must keep the original code and add a request_id.
    res = client.get("/api/host/events/evt_x/resources")
    body = res.json()
    assert body["error"]["code"] in ("EVENT_MODE_DISABLED", "FORBIDDEN", "NOT_FOUND")
    assert "request_id" in body["error"]


def test_request_id_normalization_clamps_and_sanitizes():
    assert obs.normalize_request_id("  abc-123_DEF ") == "abc-123_DEF"
    assert obs.normalize_request_id("bad;id'\"") == "badid"
    assert obs.normalize_request_id("") .startswith("req_")
    assert obs.normalize_request_id(None).startswith("req_")
    assert len(obs.normalize_request_id("x" * 200)) == 64


# ── Health checks ────────────────────────────────────────────────────────────


def test_health_exposes_subsystem_checks(client):
    body = client.get("/api/health").json()
    assert set(["lakebase", "migrations", "validators", "scoring", "sql_warehouse"]).issubset(
        body["checks"].keys()
    )
    # Validators are always available (in-process), even with no DB.
    assert "sql_assertion" in body["validator_types"]
    assert body["checks"]["validators"]["ok"] is True


# ── Structured logging ───────────────────────────────────────────────────────


def test_log_validation_emits_structured_line(caplog):
    with caplog.at_level(logging.INFO, logger="databricks-quest.observability"):
        obs.log_validation(
            request_id="req_1", event_id="evt_1", task_id="t1", team_id="team_a",
            validator_id="v1", validator_type="sql_assertion", status="passed", score_delta=100,
        )
    line = caplog.text
    assert "validation" in line and "status=passed" in line and "type=sql_assertion" in line


def test_log_scoring_emits_structured_line(caplog):
    with caplog.at_level(logging.INFO, logger="databricks-quest.observability"):
        obs.log_scoring(
            request_id="req_1", event_id="evt_1", team_id="team_a", workspace_id=None,
            source_type="validation", points_delta=100, awarded=True, reason="task_passed",
        )
    assert "scoring" in caplog.text and "awarded=True" in caplog.text


# ── SQL safety (destructive blocked, injection refused) ──────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE foo",
        "INSERT INTO foo VALUES (1)",
        "UPDATE foo SET a=1",
        "DELETE FROM foo",
        "SELECT 1; DROP TABLE foo",  # stacked
        "TRUNCATE foo",
        "GRANT ALL ON foo TO bar",
    ],
)
def test_destructive_sql_is_blocked(sql):
    with pytest.raises(safety.UnsafeSQLError):
        safety.ensure_safe_select(sql)


def test_readonly_select_is_allowed():
    assert safety.ensure_safe_select("SELECT count(*) FROM t").startswith("SELECT")
    assert safety.ensure_safe_select("WITH x AS (SELECT 1) SELECT * FROM x").startswith("WITH")


def test_template_injection_unknown_slot_is_refused():
    # A slot the server did not provide cannot be resolved → hard error. This is
    # what stops a player redirecting a check at another team's namespace.
    with pytest.raises(safety.UnsafeSQLError):
        safety.prepare_statement(
            "SELECT * FROM ${team_catalog}.${other_team}.secrets",
            {"team_catalog": "quest_ev", "team_schema": "team_red"},
        )


def test_template_value_with_metacharacters_is_refused():
    with pytest.raises(safety.UnsafeSQLError):
        safety.prepare_statement(
            "SELECT * FROM ${team_schema}.t",
            {"team_schema": "red; DROP TABLE x"},
        )


# ── Role enforcement ─────────────────────────────────────────────────────────


def test_require_admin_forbids_non_admin(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "admin_emails", lambda: {"boss@x.com"})

    class _Req:
        headers = {"X-Forwarded-Email": "player@x.com"}

    with pytest.raises(HTTPException) as exc:
        m.require_admin(_Req())
    assert exc.value.status_code == 403


def test_require_host_forbids_non_host_when_allowlist_set(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m.config, "event_mode_enabled", lambda: True)
    monkeypatch.setattr(m, "QUEST_HOST_ALLOWLIST", ["host@x.com"])

    class _Req:
        headers = {"X-Forwarded-Email": "player@x.com"}
        state = type("S", (), {})()

    with pytest.raises(HTTPException) as exc:
        m.require_host(_Req())
    assert exc.value.status_code == 403
