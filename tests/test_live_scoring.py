"""Live scoring & leaderboard (PR07).

Covers the player leaderboard endpoint shaping + frozen flag, the hint-reveal
endpoint (charge once, withhold body until revealed, no charge when scoring is
closed), the ``ScoringService.apply_hint_penalty`` idempotency/normalisation,
and the new leaderboard repo helpers against a stubbed ``db``.

Endpoints compose existing repositories; we stub the repo singletons and call
the async handlers directly (FastAPI ``Depends`` is bypassed in-process, so the
auth/event-mode guards are no-ops here).
"""

import asyncio

import pytest
from fastapi import HTTPException

from services.scoring_service import (
    ScoringService,
    hint_penalty_idempotency_key,
)


@pytest.fixture
def main_module():
    import main

    return main


def _run(coro):
    return asyncio.run(coro)


# ── /api/events/{id}/leaderboard shaping ─────────────────────────────────────


def test_event_leaderboard_shapes_rows_and_highlights_you(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(
        m.events_repo, "get_event",
        lambda e: {"event_id": "evt_1", "status": "active", "title": "T", "scoring_frozen_at": None},
    )
    monkeypatch.setattr(
        m.leaderboard_repo, "get_team_leaderboard",
        lambda e: [
            {"team_id": "team_blue", "display_name": "Blue", "total_points": 300, "rank": 1},
            {"team_id": "team_red", "display_name": "Red", "total_points": 100, "rank": 2},
        ],
    )
    monkeypatch.setattr(m.leaderboard_repo, "recent_scoring_feed", lambda e, limit=25: [
        {"scoring_event_id": "s1", "team_name": "Blue", "points_delta": 100, "source_type": "validation"},
    ])
    monkeypatch.setattr(
        m, "_resolve_attempt_identity",
        lambda e, u: {"team_id": "team_red", "team_row": {"display_name": "Red"}, "workspace_id": None},
    )

    out = _run(m.get_event_leaderboard("evt_1", request=None, _=None))
    assert out["frozen"] is False
    assert out["status"] == "active"
    assert [r["team_id"] for r in out["leaderboard"]] == ["team_blue", "team_red"]
    assert out["you"]["team_id"] == "team_red"
    assert out["you"]["rank"] == 2
    assert out["recent"][0]["scoring_event_id"] == "s1"


def test_event_leaderboard_frozen_flag_and_unscored_you(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(
        m.events_repo, "get_event",
        lambda e: {"event_id": "evt_1", "status": "completed", "title": "T", "scoring_frozen_at": None},
    )
    monkeypatch.setattr(m.leaderboard_repo, "get_team_leaderboard", lambda e: [])
    monkeypatch.setattr(m.leaderboard_repo, "recent_scoring_feed", lambda e, limit=25: [])
    monkeypatch.setattr(
        m, "_resolve_attempt_identity",
        lambda e, u: {"team_id": "team_red", "team_row": {"display_name": "Red"}, "workspace_id": None},
    )

    out = _run(m.get_event_leaderboard("evt_1", request=None, _=None))
    # completed → frozen/final
    assert out["frozen"] is True
    # on a team but no points yet → surfaced with null rank
    assert out["you"]["team_id"] == "team_red"
    assert out["you"]["rank"] is None
    assert out["you"]["total_points"] == 0


def test_event_leaderboard_scoring_frozen_at_locks_active_event(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(
        m.events_repo, "get_event",
        lambda e: {"event_id": "evt_1", "status": "active", "title": "T", "scoring_frozen_at": "2026-06-03T00:00:00"},
    )
    monkeypatch.setattr(m.leaderboard_repo, "get_team_leaderboard", lambda e: [])
    monkeypatch.setattr(m.leaderboard_repo, "recent_scoring_feed", lambda e, limit=25: [])
    monkeypatch.setattr(m, "_resolve_attempt_identity", lambda e, u: {"team_id": None, "team_row": None, "workspace_id": None})

    out = _run(m.get_event_leaderboard("evt_1", request=None, _=None))
    assert out["frozen"] is True
    assert out["you"] is None


# ── /api/events/{id}/tasks/{task}/hints/{id}/reveal ──────────────────────────


def test_reveal_hint_charges_once_when_active(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: {"event_id": "evt_1", "status": "active", "scoring_frozen_at": None})
    monkeypatch.setattr(
        m.quest_packs_repo, "get_hint",
        lambda h: {"hint_id": "hint_1", "task_id": "tsk_1", "quest_id": "q1", "title": "Tip", "body_md": "secret body", "penalty_points": -10},
    )
    monkeypatch.setattr(
        m, "_resolve_attempt_identity",
        lambda e, u: {"team_id": "team_red", "team_row": None, "workspace_id": None},
    )
    monkeypatch.setattr(m.leaderboard_repo, "get_team_score", lambda e, t: 90)
    captured = {}

    def fake_penalty(**kwargs):
        captured.update(kwargs)
        return {"applied": True, "points_delta": -10, "scoring_event_id": "score_1"}

    monkeypatch.setattr(m.default_scoring_service, "apply_hint_penalty", fake_penalty)
    monkeypatch.setattr(m, "record_audit", lambda **kw: None)

    out = _run(m.reveal_hint("evt_1", "tsk_1", "hint_1", request=None, _=None))
    assert out["revealed"] is True
    assert out["hint"]["body_md"] == "secret body"  # body returned on reveal
    assert out["penalty_applied"] == -10
    assert out["newly_applied"] is True
    assert out["team_score"] == 90
    assert captured["hint_id"] == "hint_1"
    assert captured["team_id"] == "team_red"


def test_reveal_hint_does_not_charge_when_frozen(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: {"event_id": "evt_1", "status": "frozen", "scoring_frozen_at": None})
    monkeypatch.setattr(
        m.quest_packs_repo, "get_hint",
        lambda h: {"hint_id": "hint_1", "task_id": "tsk_1", "quest_id": "q1", "title": "Tip", "body_md": "secret body", "penalty_points": -10},
    )
    monkeypatch.setattr(m, "_resolve_attempt_identity", lambda e, u: {"team_id": "team_red", "team_row": None, "workspace_id": None})
    monkeypatch.setattr(m.leaderboard_repo, "get_team_score", lambda e, t: 100)

    def boom(**kwargs):
        raise AssertionError("apply_hint_penalty must not be called when frozen")

    monkeypatch.setattr(m.default_scoring_service, "apply_hint_penalty", boom)

    out = _run(m.reveal_hint("evt_1", "tsk_1", "hint_1", request=None, _=None))
    # Body still returned (player can read it) but no penalty incurred.
    assert out["hint"]["body_md"] == "secret body"
    assert out["penalty_applied"] == 0
    assert out["newly_applied"] is False


def test_reveal_hint_404_on_task_mismatch(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m, "get_user_email", lambda req: "u@x")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: {"event_id": "evt_1", "status": "active", "scoring_frozen_at": None})
    monkeypatch.setattr(
        m.quest_packs_repo, "get_hint",
        lambda h: {"hint_id": "hint_1", "task_id": "OTHER_TASK", "body_md": "x", "penalty_points": -10},
    )
    with pytest.raises(HTTPException) as exc:
        _run(m.reveal_hint("evt_1", "tsk_1", "hint_1", request=None, _=None))
    assert exc.value.status_code == 404


# ── ScoringService.apply_hint_penalty ────────────────────────────────────────


class _FakeLedger:
    """Mimics UNIQUE(idempotency_key) ON CONFLICT DO NOTHING."""

    def __init__(self):
        self.keys = set()
        self.rows = []

    def execute(self, sql, params=()):
        up = " ".join(sql.split()).upper()
        assert up.startswith("INSERT INTO SCORING_EVENTS")
        key = params[-1]
        if key in self.keys:
            return 0
        self.keys.add(key)
        self.rows.append(params)
        return 1


@pytest.fixture()
def ledger(monkeypatch):
    fake = _FakeLedger()
    import db

    monkeypatch.setattr(db, "execute", fake.execute)
    return fake


def test_hint_penalty_key_team_scoped():
    assert (
        hint_penalty_idempotency_key("evt_1", "hint_1", team_id="team_red")
        == "hint:team_red:evt_1:hint_1"
    )


def test_hint_penalty_charges_once_then_idempotent(ledger):
    svc = ScoringService()
    first = svc.apply_hint_penalty(event_id="evt_1", hint_id="hint_1", penalty_points=-10, team_id="team_red")
    assert first["applied"] is True
    assert first["points_delta"] == -10
    # Re-reveal → no double-charge.
    second = svc.apply_hint_penalty(event_id="evt_1", hint_id="hint_1", penalty_points=-10, team_id="team_red")
    assert second["applied"] is False
    assert second["points_delta"] == 0
    assert len(ledger.rows) == 1


def test_hint_penalty_normalises_positive_magnitude(ledger):
    svc = ScoringService()
    # Author wrote a positive penalty; it must still subtract.
    out = svc.apply_hint_penalty(event_id="evt_1", hint_id="hint_pos", penalty_points=15, team_id="team_red")
    assert out["points_delta"] == -15


def test_hint_penalty_zero_is_noop(ledger):
    svc = ScoringService()
    out = svc.apply_hint_penalty(event_id="evt_1", hint_id="hint_free", penalty_points=0, team_id="team_red")
    assert out["applied"] is False
    assert len(ledger.rows) == 0


def test_hint_penalty_no_scope_is_noop(ledger):
    svc = ScoringService()
    out = svc.apply_hint_penalty(event_id="evt_1", hint_id="hint_1", penalty_points=-10)
    assert out["applied"] is False
    assert len(ledger.rows) == 0


# ── Leaderboard repo helpers ─────────────────────────────────────────────────


def test_revealed_hint_ids_filters_and_dedupes(monkeypatch):
    import db
    from repositories.leaderboard import LeaderboardRepository

    monkeypatch.setattr(
        db, "execute_query",
        lambda sql, params=(): [{"source_id": "hint_1"}, {"source_id": "hint_2"}],
    )
    repo = LeaderboardRepository()
    assert set(repo.revealed_hint_ids("evt_1", "team_red")) == {"hint_1", "hint_2"}


def test_revealed_hint_ids_empty_without_team():
    from repositories.leaderboard import LeaderboardRepository

    assert LeaderboardRepository().revealed_hint_ids("evt_1", "") == []
