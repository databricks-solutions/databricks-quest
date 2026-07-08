"""Federation HTTP surface (PR-pilot G).

Covers ``/api/federation/status`` and ``/api/federation/leaderboard`` plus the
master-only ``require_master_host`` gate (a child role 404s on roster import),
driven through the real FastAPI stack with ``TestClient``. The federation
service + repos are faked so no shared Postgres is needed, but routing,
event-mode gating, role gating, and response shaping are the real code paths.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def main_module():
    import main

    return main


@pytest.fixture()
def client(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m.config, "event_mode_enabled", lambda: True)
    monkeypatch.setattr(m.config, "is_child", lambda: False)
    return TestClient(m.app, raise_server_exceptions=False)


# ── /api/federation/status ────────────────────────────────────────────────────


def test_federation_status_reports_role_and_db_flag(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m.fed, "child_status", lambda submitted_by: {"role": "child", "mapped": True})
    monkeypatch.setattr(m.db, "healthcheck", lambda: True)

    res = client.get("/api/federation/status", headers={"X-Forwarded-Email": "lab@x.com"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["role"] == "child"
    assert body["db_connected"] is True
    assert body["event_mode"] is True
    assert body["submitted_by"] == "lab@x.com"


def test_federation_status_db_flag_false_on_error(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m.fed, "child_status", lambda submitted_by: {"role": "child"})

    def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(m.db, "healthcheck", boom)
    body = client.get("/api/federation/status", headers={"X-Forwarded-Email": "lab@x.com"}).json()
    assert body["db_connected"] is False


def test_federation_status_404_when_event_mode_off(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m.config, "event_mode_enabled", lambda: False)
    res = client.get("/api/federation/status", headers={"X-Forwarded-Email": "lab@x.com"})
    assert res.status_code == 404


# ── /api/federation/leaderboard ───────────────────────────────────────────────


def test_federation_leaderboard_unresolved_event_is_empty(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m.fed, "resolve_event_id", lambda *a, **k: None)
    body = client.get("/api/federation/leaderboard", headers={"X-Forwarded-Email": "lab@x.com"}).json()
    assert body["leaderboard"] == []
    assert body["mapped"] is False
    assert body["event_id"] is None


def test_federation_leaderboard_returns_rows_and_you(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m.fed, "resolve_event_id", lambda *a, **k: "evt_1")
    monkeypatch.setattr(
        m.leaderboard_repo, "get_team_leaderboard",
        lambda e: [
            {"team_id": "team_red", "display_name": "Red", "total_points": 200, "rank": 1},
            {"team_id": "team_blue", "display_name": "Blue", "total_points": 100, "rank": 2},
        ],
    )
    monkeypatch.setattr(m.config, "QUEST_WORKSPACE_ID", "ws_child_1")
    monkeypatch.setattr(
        m.federation_repo, "resolve_identity",
        lambda eid, ws, user: {"team_id": "team_blue", "team_display_name": "Blue"},
    )

    body = client.get(
        "/api/federation/leaderboard?event=pilot",
        headers={"X-Forwarded-Email": "lab@x.com"},
    ).json()
    assert [r["team_id"] for r in body["leaderboard"]] == ["team_red", "team_blue"]
    assert body["mapped"] is True
    assert body["you"]["team_id"] == "team_blue"
    assert body["you"]["rank"] == 2


def test_federation_leaderboard_mapped_but_unscored_team(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m.fed, "resolve_event_id", lambda *a, **k: "evt_1")
    monkeypatch.setattr(m.leaderboard_repo, "get_team_leaderboard", lambda e: [])
    monkeypatch.setattr(m.config, "QUEST_WORKSPACE_ID", "ws_child_1")
    monkeypatch.setattr(
        m.federation_repo, "resolve_identity",
        lambda eid, ws, user: {"team_id": "team_green", "team_display_name": "Green"},
    )
    body = client.get("/api/federation/leaderboard", headers={"X-Forwarded-Email": "lab@x.com"}).json()
    assert body["mapped"] is True
    assert body["you"]["team_id"] == "team_green"
    assert body["you"]["rank"] is None
    assert body["you"]["total_points"] == 0


# ── master-only roster import gate ────────────────────────────────────────────


def test_roster_import_404_on_child_role(client, main_module, monkeypatch):
    m = main_module
    # A child deployment must not expose the master roster-import surface.
    monkeypatch.setattr(m.config, "is_child", lambda: True)
    res = client.post(
        "/api/host/events/evt_1/roster/import",
        json={"csv": "workspace_id,lab_user_email,team_name\n"},
        headers={"X-Forwarded-Email": "host@x.com"},
    )
    assert res.status_code == 404
