"""Player gameplay read endpoints + repo helpers (PR05).

The three new player reads (``/team``, ``/quests``, ``/quests/{id}``) compose
existing repositories. We test the endpoint shaping/aggregation by stubbing the
repo singletons and calling the async handlers directly (FastAPI's ``Depends``
is bypassed when called in-process, so ``require_event_mode`` is a no-op here),
and test the new repo helpers against a stubbed ``db.execute_query``.
"""

import asyncio

import pytest
from fastapi import HTTPException


@pytest.fixture
def main_module():
    import main

    return main


def _run(coro):
    return asyncio.run(coro)


# ── /api/events/{id}/quests aggregation ──────────────────────────────────────


def test_list_event_quests_counts_team_progress(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: {"pack_version_id": "pv1", "status": "active"})
    monkeypatch.setattr(m.events_repo, "get_team_for_user", lambda e, u: {"team_id": "team_red"})
    monkeypatch.setattr(m.leaderboard_repo, "completed_task_ids", lambda e, t: ["t1"])
    monkeypatch.setattr(m.quest_packs_repo, "list_quests", lambda pv: [{"quest_id": "q1", "title": "Q1"}])
    monkeypatch.setattr(
        m.quest_packs_repo, "list_tasks",
        lambda q: [{"task_id": "t1"}, {"task_id": "t2"}],
    )

    out = _run(m.list_event_quests("evt_1", request=None, _=None))
    q = out["quests"][0]
    assert q["task_count"] == 2
    assert q["completed_tasks"] == 1
    assert q["complete"] is False
    assert out["attempts_open"] is True
    assert out["team_id"] == "team_red"


def test_list_event_quests_marks_complete_when_all_tasks_done(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: {"pack_version_id": "pv1", "status": "paused"})
    monkeypatch.setattr(m.events_repo, "get_team_for_user", lambda e, u: {"team_id": "team_red"})
    monkeypatch.setattr(m.leaderboard_repo, "completed_task_ids", lambda e, t: ["t1", "t2"])
    monkeypatch.setattr(m.quest_packs_repo, "list_quests", lambda pv: [{"quest_id": "q1", "title": "Q1"}])
    monkeypatch.setattr(m.quest_packs_repo, "list_tasks", lambda q: [{"task_id": "t1"}, {"task_id": "t2"}])

    out = _run(m.list_event_quests("evt_1", request=None, _=None))
    assert out["quests"][0]["complete"] is True
    assert out["attempts_open"] is False  # paused


# ── /api/events/{id}/team shaping ────────────────────────────────────────────


def test_get_event_team_shapes_dashboard_and_filters_recent(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(m.events_repo, "get_participant", lambda e, u: {"participant_id": "p1"})
    monkeypatch.setattr(
        m.events_repo, "get_team_for_user",
        lambda e, u: {"team_id": "team_red", "name": "Red", "display_name": "Red", "color": "#f00"},
    )
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: {"pack_version_id": "pv1", "status": "active"})
    monkeypatch.setattr(m.events_repo, "event_counts", lambda e, pv: {"tasks": 4})
    monkeypatch.setattr(m.events_repo, "list_team_members", lambda t: [{"user_id": "u@x", "display_name": "U", "role": "player"}])
    monkeypatch.setattr(m.leaderboard_repo, "completed_task_ids", lambda e, t: ["t1", "t2"])
    monkeypatch.setattr(m.leaderboard_repo, "get_team_score", lambda e, t: 200)
    monkeypatch.setattr(m.leaderboard_repo, "get_team_rank", lambda e, t: 2)
    monkeypatch.setattr(
        m.leaderboard_repo, "list_recent_scoring_events",
        lambda e, limit=50: [
            {"scoring_event_id": "s1", "team_id": "team_red", "points_delta": 100, "reason": "task"},
            {"scoring_event_id": "s2", "team_id": "team_blue", "points_delta": 50, "reason": "task"},
        ],
    )

    out = _run(m.get_event_team("evt_1", request=None, _=None))
    assert out["team"]["display_name"] == "Red"
    assert out["score"] == 200
    assert out["rank"] == 2
    assert out["progress"] == {"completed_tasks": 2, "total_tasks": 4}
    assert [r["scoring_event_id"] for r in out["recent"]] == ["s1"]  # team_blue filtered out


def test_get_event_team_when_not_on_team(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(m.events_repo, "get_participant", lambda e, u: {"participant_id": "p1"})
    monkeypatch.setattr(m.events_repo, "get_team_for_user", lambda e, u: None)

    out = _run(m.get_event_team("evt_1", request=None, _=None))
    assert out["team"] is None
    assert out["joined"] is True


# ── /api/events/{id}/quests/{quest_id} detail + 404 scoping ──────────────────


def test_get_event_quest_404_when_quest_belongs_to_other_pack(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: {"pack_version_id": "pv1", "status": "active"})
    monkeypatch.setattr(m.quest_packs_repo, "get_quest", lambda q: {"pack_version_id": "pvX"})

    with pytest.raises(HTTPException) as exc:
        _run(m.get_event_quest("evt_1", "q9", request=None, _=None))
    assert exc.value.status_code == 404


def test_get_event_quest_includes_hints_and_completion(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: {"pack_version_id": "pv1", "status": "active"})
    monkeypatch.setattr(m.quest_packs_repo, "get_quest", lambda q: {"quest_id": "q1", "pack_version_id": "pv1", "title": "Q1"})
    monkeypatch.setattr(m.events_repo, "get_team_for_user", lambda e, u: {"team_id": "team_red"})
    monkeypatch.setattr(m.leaderboard_repo, "completed_task_ids", lambda e, t: ["t1"])
    monkeypatch.setattr(
        m.quest_packs_repo, "list_tasks_detail",
        lambda q: [{"task_id": "t1", "title": "T1"}, {"task_id": "t2", "title": "T2"}],
    )
    monkeypatch.setattr(
        m.quest_packs_repo, "list_hints",
        lambda tid: [
            {"hint_id": "h_shown", "title": "h", "body_md": "look here", "penalty_points": -5, "sort_order": 1},
            {"hint_id": "h_hidden", "title": "h2", "body_md": "still secret", "penalty_points": -5, "sort_order": 2},
        ] if tid == "t2" else [],
    )
    # team has revealed only the first hint → its body is included, the other withheld.
    monkeypatch.setattr(m.leaderboard_repo, "revealed_hint_ids", lambda e, t: ["h_shown"])

    out = _run(m.get_event_quest("evt_1", "q1", request=None, _=None))
    tasks = {t["task_id"]: t for t in out["tasks"]}
    assert tasks["t1"]["complete"] is True
    assert tasks["t2"]["complete"] is False
    hints = {h["hint_id"]: h for h in tasks["t2"]["hints"]}
    assert hints["h_shown"]["revealed"] is True
    assert hints["h_shown"]["body_md"] == "look here"
    # Unrevealed hint body is withheld so the penalty can't be dodged.
    assert hints["h_hidden"]["revealed"] is False
    assert hints["h_hidden"]["body_md"] is None


# ── Repo helper unit tests (stubbed db.execute_query) ────────────────────────


def test_completed_task_ids_short_circuits_without_team(monkeypatch):
    import db
    from repositories.leaderboard import LeaderboardRepository

    def boom(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("db should not be hit for empty team")

    monkeypatch.setattr(db, "execute_query", boom)
    assert LeaderboardRepository().completed_task_ids("evt_1", "") == []


def test_completed_task_ids_extracts_ids(monkeypatch):
    import db
    from repositories.leaderboard import LeaderboardRepository

    monkeypatch.setattr(db, "execute_query", lambda *a, **k: [{"task_id": "t1"}, {"task_id": "t2"}, {"task_id": None}])
    assert LeaderboardRepository().completed_task_ids("evt_1", "team_red") == ["t1", "t2"]


def test_get_team_rank_parses_rank(monkeypatch):
    import db
    from repositories.leaderboard import LeaderboardRepository

    monkeypatch.setattr(db, "execute_query", lambda *a, **k: [{"rank": 3}])
    assert LeaderboardRepository().get_team_rank("evt_1", "team_red") == 3
    monkeypatch.setattr(db, "execute_query", lambda *a, **k: [])
    assert LeaderboardRepository().get_team_rank("evt_1", "team_red") is None


def test_list_hints_and_get_quest(monkeypatch):
    import db
    from repositories.quest_packs import QuestPacksRepository

    monkeypatch.setattr(db, "execute_query", lambda *a, **k: [{"hint_id": "h1", "body_md": "x"}])
    assert QuestPacksRepository().list_hints("t1")[0]["hint_id"] == "h1"

    monkeypatch.setattr(db, "execute_query", lambda *a, **k: [{"quest_id": "q1", "narrative": "story"}])
    assert QuestPacksRepository().get_quest("q1")["narrative"] == "story"
