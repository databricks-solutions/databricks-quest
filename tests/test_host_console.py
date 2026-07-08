"""Host console endpoints + repo helpers (PR06).

Endpoints compose existing repositories; we stub the repo singletons and call
the async handlers directly (FastAPI ``Depends`` is bypassed in-process, so
``require_host`` is a no-op here). Repo helpers are tested against a stubbed
``db`` module.
"""

import asyncio
from contextlib import contextmanager

import pytest
from fastapi import HTTPException


@pytest.fixture
def main_module():
    import main

    return main


def _run(coro):
    return asyncio.run(coro)


# ── /api/host/events/{id} overview shaping ───────────────────────────────────


def test_host_overview_shapes_dashboard(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: {"event_id": "evt_1", "status": "active", "pack_version_id": "pv1", "title": "T"})
    monkeypatch.setattr(m.events_repo, "event_counts", lambda e, pv: {"participants": 5, "teams": 2, "quests": 3, "tasks": 9})
    monkeypatch.setattr(m.events_repo, "list_teams_with_counts", lambda e: [
        {"team_id": "team_red", "name": "Red", "display_name": "Red", "color": "#f00", "members": 3},
        {"team_id": "team_blue", "name": "Blue", "display_name": "Blue", "color": "#00f", "members": 2},
    ])
    monkeypatch.setattr(m.leaderboard_repo, "get_team_leaderboard", lambda e: [
        {"team_id": "team_blue", "rank": 1}, {"team_id": "team_red", "rank": 2},
    ])
    monkeypatch.setattr(m.leaderboard_repo, "get_team_score", lambda e, t: 100 if t == "team_blue" else 50)
    monkeypatch.setattr(m.attempts_repo, "attempt_status_counts", lambda e: {"passed": 4, "failed": 1})
    monkeypatch.setattr(m.announcements_repo, "list_for_event", lambda e, limit=10: [])

    out = _run(m.host_event_overview("evt_1", user="h@x"))
    # active → allowed transitions include pause/freeze/complete (sorted)
    assert "paused" in out["allowed_transitions"]
    assert out["counts"]["tasks"] == 9
    # teams sorted by rank ascending → blue (rank 1) first
    assert out["teams"][0]["team_id"] == "team_blue"
    assert out["teams"][0]["score"] == 100
    assert out["attempt_status_counts"]["passed"] == 4


def test_host_overview_404_for_missing_event(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: None)
    with pytest.raises(HTTPException) as exc:
        _run(m.host_event_overview("evt_1", user="h@x"))
    assert exc.value.status_code == 404


# ── /api/host/events/{id}/attempts ───────────────────────────────────────────


def test_host_list_attempts_clamps_limit_and_passes_status(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    seen = {}

    def fake_list(event_id, status=None, limit=100):
        seen["status"] = status
        seen["limit"] = limit
        return [{"attempt_id": "a1", "status": status or "passed"}]

    monkeypatch.setattr(m.attempts_repo, "list_event_attempts", fake_list)
    monkeypatch.setattr(m.attempts_repo, "attempt_status_counts", lambda e: {"failed": 2})

    out = _run(m.host_list_attempts("evt_1", status="failed", limit=99999, user="h@x"))
    assert seen["status"] == "failed"
    assert seen["limit"] == 500  # clamped
    assert out["attempts"][0]["attempt_id"] == "a1"


# ── announcements ─────────────────────────────────────────────────────────


def test_host_create_announcement_requires_title_and_body(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    body = m.AnnouncementPayload(title="  ", body_md="something")
    with pytest.raises(HTTPException) as exc:
        _run(m.host_create_announcement("evt_1", body, user="h@x"))
    assert exc.value.status_code == 400


def test_host_create_announcement_persists_and_audits(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    created = {}
    monkeypatch.setattr(
        m.announcements_repo, "create",
        lambda **kw: {"announcement_id": "ann_1", "severity": kw.get("severity", "info"), "title": kw["title"]},
    )
    monkeypatch.setattr(m, "record_audit", lambda **kw: created.update(kw))

    body = m.AnnouncementPayload(title="Heads up", body_md="Submissions close in 5", severity="warning")
    out = _run(m.host_create_announcement("evt_1", body, user="h@x"))
    assert out["announcement_id"] == "ann_1"
    assert created["action"] == "announcement.create"


# ── manual score adjustment ───────────────────────────────────────────────


def test_host_adjust_requires_reason(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    body = m.AdjustmentPayload(team_id="team_red", points_delta=10, reason="   ")
    with pytest.raises(HTTPException) as exc:
        _run(m.host_adjust_score("evt_1", body, user="h@x"))
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "REASON_REQUIRED"


def test_host_adjust_rejects_zero_delta(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    body = m.AdjustmentPayload(team_id="team_red", points_delta=0, reason="typo")
    with pytest.raises(HTTPException) as exc:
        _run(m.host_adjust_score("evt_1", body, user="h@x"))
    assert exc.value.detail["error"]["code"] == "ZERO_DELTA"


def test_host_adjust_404_when_team_not_in_event(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m.events_repo, "get_team", lambda t: {"team_id": t, "event_id": "other"})
    body = m.AdjustmentPayload(team_id="team_red", points_delta=10, reason="bonus")
    with pytest.raises(HTTPException) as exc:
        _run(m.host_adjust_score("evt_1", body, user="h@x"))
    assert exc.value.detail["error"]["code"] == "TEAM_NOT_FOUND"


def test_host_adjust_records_and_audits(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m.events_repo, "get_team", lambda t: {"team_id": t, "event_id": "evt_1"})
    monkeypatch.setattr(
        m.scoring_repo, "record_manual_adjustment",
        lambda **kw: {"adjustment_id": "adj_1", "scoring_event_id": "score_1", "points_delta": kw["points_delta"]},
    )
    audited = {}
    monkeypatch.setattr(m, "record_audit", lambda **kw: audited.update(kw))

    body = m.AdjustmentPayload(team_id="team_red", points_delta=-25, reason="penalty: late")
    out = _run(m.host_adjust_score("evt_1", body, user="h@x"))
    assert out["adjustment_id"] == "adj_1"
    assert out["points_delta"] == -25
    assert audited["action"] == "score.adjust"
    assert audited["payload"]["points_delta"] == -25


# ── repo helpers ─────────────────────────────────────────────────────────


def test_announcements_create_coerces_invalid_severity(monkeypatch):
    import db
    from repositories.announcements import AnnouncementsRepository

    monkeypatch.setattr(db, "execute", lambda *a, **k: 1)
    row = AnnouncementsRepository().create(
        event_id="evt_1", title="Hi", body_md="body", created_by="h@x", severity="purple"
    )
    assert row["severity"] == "info"  # invalid coerced to info
    assert row["title"] == "Hi"


def test_record_manual_adjustment_writes_audit_and_ledger(monkeypatch):
    import db
    from repositories.scoring import ScoringRepository

    statements = []

    class FakeCursor:
        def execute(self, sql, params):
            statements.append((sql, params))

    @contextmanager
    def fake_tx():
        yield FakeCursor()

    monkeypatch.setattr(db, "transaction", fake_tx)
    out = ScoringRepository().record_manual_adjustment(
        event_id="evt_1", points_delta=50, reason="bonus", created_by="h@x", team_id="team_red"
    )
    assert out["adjustment_id"].startswith("adj_")
    assert len(statements) == 2  # manual_adjustments + scoring_events
    assert "manual_adjustments" in statements[0][0]
    assert "scoring_events" in statements[1][0]


def test_list_event_attempts_filters_by_status(monkeypatch):
    import db
    from repositories.attempts import AttemptsRepository

    seen = {}

    def fake_q(sql, params):
        seen["sql"] = sql
        seen["params"] = params
        return [{"attempt_id": "a1"}]

    monkeypatch.setattr(db, "execute_query", fake_q)
    AttemptsRepository().list_event_attempts("evt_1", status="failed", limit=10)
    assert "a.status = %s" in seen["sql"]
    assert seen["params"] == ("evt_1", "failed", 10)


def test_attempt_status_counts_parses_rows(monkeypatch):
    import db
    from repositories.attempts import AttemptsRepository

    monkeypatch.setattr(db, "execute_query", lambda *a, **k: [{"status": "passed", "c": 3}, {"status": "failed", "c": 1}])
    counts = AttemptsRepository().attempt_status_counts("evt_1")
    assert counts == {"passed": 3, "failed": 1}
