"""Field reporting & hunter signaling (PR11).

The report builder and renderers are pure, so they're tested directly against
synthetic rows. The endpoint composes repos + the builder; we stub the repo
singletons and call the async handlers in-process (FastAPI ``Depends`` is a
no-op when calling the handler directly).
"""

import asyncio
import csv
import io
import json

import pytest
from fastapi import HTTPException

from services import report_service as rs


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def main_module():
    import main

    return main


# Synthetic event with two quests / three tasks and two teams.
EVENT = {
    "event_id": "evt_1",
    "slug": "ai-bi-day",
    "title": "AI/BI GameDay",
    "status": "completed",
    "pack_version_id": "pv_1",
}
TEAMS = [
    {"team_id": "team_red", "display_name": "Red Team", "members": 3},
    {"team_id": "team_blue", "display_name": "Blue Team", "members": 2},
]
LEADERBOARD = [
    {"team_id": "team_red", "display_name": "Red Team", "total_points": 120, "rank": 1, "last_scored_at": None},
    {"team_id": "team_blue", "display_name": "Blue Team", "total_points": 40, "rank": 2, "last_scored_at": None},
]
CATALOG = [
    {"quest_slug": "q1", "quest_title": "Warm up", "quest_sort": 0, "task_id": "t1", "task_slug": "t1", "task_title": "First query", "points": 20, "task_sort": 0},
    {"quest_slug": "q1", "quest_title": "Warm up", "quest_sort": 0, "task_id": "t2", "task_slug": "t2", "task_title": "Aggregate", "points": 40, "task_sort": 1},
    {"quest_slug": "q2", "quest_title": "Dashboards", "quest_sort": 1, "task_id": "t3", "task_slug": "t3", "task_title": "Build dashboard", "points": 60, "task_sort": 0},
]
# Red solved all 3; Blue only the first.
COMPLETION = [
    {"team_id": "team_red", "task_id": "t1"},
    {"team_id": "team_red", "task_id": "t2"},
    {"team_id": "team_red", "task_id": "t3"},
    {"team_id": "team_blue", "task_id": "t1"},
]
FAILURES = [
    {"task_id": "t3", "task_title": "Build dashboard", "status": "failed", "attempts": 4},
]
HINTS = [
    {"team_id": "team_blue", "team_name": "Blue Team", "hint_id": "h1", "task_id": "t3", "task_title": "Build dashboard", "points_delta": -10, "created_at": None},
]
FIRST_SOLVES = [
    {"task_id": "t1", "team_id": "team_red", "created_at": None},
    {"task_id": "t2", "team_id": "team_red", "created_at": None},
    {"task_id": "t3", "team_id": "team_red", "created_at": None},
]
STATUS_COUNTS = {"passed": 4, "failed": 1}
COUNTS = {"participants": 5, "teams": 2, "quests": 2, "tasks": 3}


def _build():
    return rs.build_report(
        event=EVENT,
        teams=TEAMS,
        leaderboard=LEADERBOARD,
        task_catalog=CATALOG,
        completion_pairs=COMPLETION,
        failures=FAILURES,
        hint_usage=HINTS,
        first_solves=FIRST_SOLVES,
        status_counts=STATUS_COUNTS,
        counts=COUNTS,
    )


# ── Pure builder ─────────────────────────────────────────────────────────────


def test_summary_carries_counts_and_attempts():
    r = _build()
    s = r["summary"]
    assert s["title"] == "AI/BI GameDay"
    assert s["teams"] == 2 and s["participants"] == 5
    assert s["quests"] == 2 and s["tasks"] == 3
    assert s["attempts"] == 5  # 4 passed + 1 failed
    assert s["attempts_by_status"] == STATUS_COUNTS


def test_completion_matrix_counts_and_pct():
    r = _build()
    by_team = {m["team_id"]: m for m in r["completion_matrix"]}
    assert by_team["team_red"]["completed_count"] == 3
    assert by_team["team_red"]["completion_pct"] == 100.0
    assert by_team["team_blue"]["completed_count"] == 1
    assert by_team["team_blue"]["completion_pct"] == pytest.approx(33.3, abs=0.1)


def test_blockers_ordered_hardest_first():
    r = _build()
    # t3 was solved by 1 team and drew 4 failed attempts → hardest, ranked first.
    assert r["blockers"][0]["task_id"] == "t3"
    assert r["blockers"][0]["solved_teams"] == 1
    assert r["blockers"][0]["failed_attempts"] == 4


def test_champions_and_fastest_team():
    r = _build()
    assert r["champions"][0]["team_name"] == "Red Team"
    assert r["champions"][0]["rank"] == 1
    # Red took every first solve.
    assert r["fastest_team"]["team_id"] == "team_red"
    assert r["fastest_team"]["first_solves"] == 3


def test_hint_usage_and_total_penalty():
    r = _build()
    assert len(r["hint_usage"]) == 1
    assert r["hint_total_penalty"] == -10
    assert r["hint_usage"][0]["team_name"] == "Blue Team"


def test_follow_ups_flag_blocker_and_weak_team():
    r = _build()
    joined = " ".join(r["recommended_follow_ups"]).lower()
    assert "build dashboard" in joined  # hardest task surfaced
    assert "blue team" in joined        # <50% completion team surfaced
    assert any("recognise" in f.lower() or "champion" in f.lower() for f in r["recommended_follow_ups"])


def test_follow_ups_celebrate_when_no_blockers():
    r = rs.build_report(
        event=EVENT,
        teams=[TEAMS[0]],
        leaderboard=[LEADERBOARD[0]],
        task_catalog=CATALOG,
        completion_pairs=[{"team_id": "team_red", "task_id": tid} for tid in ("t1", "t2", "t3")],
        failures=[],
        hint_usage=[],
        first_solves=FIRST_SOLVES,
        status_counts={"passed": 3},
        counts={"participants": 3, "teams": 1, "quests": 2, "tasks": 3},
    )
    joined = " ".join(r["recommended_follow_ups"]).lower()
    assert "harder pack" in joined or "no blockers" in joined


# ── Renderers ────────────────────────────────────────────────────────────────


def test_render_json_roundtrips():
    r = _build()
    parsed = json.loads(rs.render_json(r))
    assert parsed["summary"]["title"] == "AI/BI GameDay"
    assert len(parsed["leaderboard"]) == 2


def test_render_csv_includes_teams_tasks_scores():
    r = _build()
    text = rs.render_csv(r)
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    assert "rank" in header and "points" in header
    # Each task title is a column in the matrix.
    for title in ("First query", "Aggregate", "Build dashboard"):
        assert title in header
    # Red team row shows full completion (three 1s for the task columns).
    red = next(row for row in rows[1:] if row[1] == "Red Team")
    assert red[2] == "120"  # points
    assert red[-3:] == ["1", "1", "1"]


def test_render_csv_neutralises_formula_injection():
    evil = dict(EVENT)
    teams = [{"team_id": "t_x", "display_name": "=cmd|calc", "members": 1}]
    lb = [{"team_id": "t_x", "display_name": "=cmd|calc", "total_points": 1, "rank": 1, "last_scored_at": None}]
    r = rs.build_report(
        event=evil, teams=teams, leaderboard=lb, task_catalog=CATALOG,
        completion_pairs=[], failures=[], hint_usage=[], first_solves=[],
        status_counts={}, counts={},
    )
    text = rs.render_csv(r)
    assert "'=cmd|calc" in text  # leading quote neutralises the formula


def test_render_markdown_is_readable():
    r = _build()
    md = rs.render_markdown(r)
    assert "# Event report" in md
    assert "## Leaderboard" in md
    assert "## Recommended follow-ups" in md
    assert "Red Team" in md


# ── Endpoints ────────────────────────────────────────────────────────────────


def _stub_repos(m, monkeypatch):
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: EVENT)
    monkeypatch.setattr(m.events_repo, "list_teams_with_counts", lambda e: TEAMS)
    monkeypatch.setattr(m.events_repo, "event_counts", lambda e, pv: COUNTS)
    monkeypatch.setattr(m.leaderboard_repo, "get_team_leaderboard", lambda e: LEADERBOARD)
    monkeypatch.setattr(m.reporting_repo, "task_catalog", lambda pv: CATALOG)
    monkeypatch.setattr(m.reporting_repo, "team_task_completion", lambda e: COMPLETION)
    monkeypatch.setattr(m.reporting_repo, "failure_summary", lambda e: FAILURES)
    monkeypatch.setattr(m.reporting_repo, "hint_usage", lambda e: HINTS)
    monkeypatch.setattr(m.reporting_repo, "first_solves", lambda e: FIRST_SOLVES)
    monkeypatch.setattr(m.attempts_repo, "attempt_status_counts", lambda e: STATUS_COUNTS)
    monkeypatch.setattr(m, "record_audit", lambda **kw: None)


def test_report_endpoint_returns_structured_report(main_module, monkeypatch):
    m = main_module
    _stub_repos(m, monkeypatch)
    out = _run(m.host_event_report("evt_1", user="h@x"))
    assert out["summary"]["title"] == "AI/BI GameDay"
    assert out["champions"][0]["team_name"] == "Red Team"
    assert out["blockers"][0]["task_id"] == "t3"


def test_export_json_sets_attachment_header(main_module, monkeypatch):
    m = main_module
    _stub_repos(m, monkeypatch)
    resp = _run(m.host_event_report_export("evt_1", format="json", user="h@x"))
    assert resp.media_type == "application/json"
    assert "ai-bi-day-report.json" in resp.headers["content-disposition"]
    parsed = json.loads(resp.body.decode())
    assert parsed["summary"]["slug"] == "ai-bi-day"


def test_export_csv_content_type(main_module, monkeypatch):
    m = main_module
    _stub_repos(m, monkeypatch)
    resp = _run(m.host_event_report_export("evt_1", format="csv", user="h@x"))
    assert resp.media_type == "text/csv"
    assert "ai-bi-day-report.csv" in resp.headers["content-disposition"]
    assert b"Red Team" in resp.body


def test_export_markdown_content(main_module, monkeypatch):
    m = main_module
    _stub_repos(m, monkeypatch)
    resp = _run(m.host_event_report_export("evt_1", format="markdown", user="h@x"))
    assert "ai-bi-day-report.md" in resp.headers["content-disposition"]
    assert b"# Event report" in resp.body


def test_export_rejects_bad_format(main_module, monkeypatch):
    m = main_module
    _stub_repos(m, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _run(m.host_event_report_export("evt_1", format="pdf", user="h@x"))
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "BAD_FORMAT"


def test_report_404_when_event_missing(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: None)
    with pytest.raises(HTTPException) as exc:
        _run(m.host_event_report("evt_1", user="h@x"))
    assert exc.value.status_code == 404
