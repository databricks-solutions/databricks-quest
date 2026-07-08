"""Core gameplay loop over HTTP (PR-pilot G — highest-value integration test).

Drives the player loop through the real FastAPI stack with ``TestClient``:

    submit (pass) → score → submit again (idempotent) → submit (fail) →
    two-team leaderboard ordering → attempt status read.

Repo singletons and the scoring service are replaced with a small in-memory
"world" so the test is DB-free, but the **request routing, dependency wiring,
validation engine, aggregation, scoring idempotency, and response shaping** are
all the production code paths. Before this, ``submit_attempt`` had zero HTTP
coverage.

Also unit-tests the P0 validator-variable namespace fallback (B): a team whose
``team_catalog``/``team_schema`` columns are NULL still resolves to the
bootstrapped FQN, so ``${team_catalog}.${team_schema}`` is never blank right
after an event opens.
"""

import pytest
from fastapi.testclient import TestClient

from services.validation_engine import ValidationEngine


# ── in-memory world ───────────────────────────────────────────────────────────


class World:
    def __init__(self):
        self.points = {}            # team_id -> total points
        self.awarded = set()        # (team_id, task_id) idempotency guard
        self.attempts = {}          # attempt_id -> attempt dict
        self.results = {}           # attempt_id -> [result rows]
        self._n = 0

    def next_id(self, prefix):
        self._n += 1
        return f"{prefix}_{self._n}"


# Two tasks: one whose SQL count clears its threshold (pass), one that doesn't.
_TASKS = {
    "task_pass": {"task_id": "task_pass", "quest_id": "q1", "points": 100},
    "task_pass2": {"task_id": "task_pass2", "quest_id": "q1", "points": 100},
    "task_fail": {"task_id": "task_fail", "quest_id": "q1", "points": 100},
}

# sql_assertion validators; the fake executor always returns cnt=1500, so the
# pass tasks expect >=1000 and the fail task expects >=5000.
_VALIDATORS = {
    "task_pass": [{
        "validator_id": "v_pass", "type": "sql_assertion", "mode": "sync",
        "config_json": {"statement": "SELECT COUNT(*) AS cnt FROM ${team_schema}.gold"},
        "expected_json": {"operator": ">=", "value": 1000}, "timeout_seconds": 30,
    }],
    "task_pass2": [{
        "validator_id": "v_pass2", "type": "sql_assertion", "mode": "sync",
        "config_json": {"statement": "SELECT COUNT(*) AS cnt FROM ${team_schema}.silver"},
        "expected_json": {"operator": ">=", "value": 1000}, "timeout_seconds": 30,
    }],
    "task_fail": [{
        "validator_id": "v_fail", "type": "sql_assertion", "mode": "sync",
        "config_json": {"statement": "SELECT COUNT(*) AS cnt FROM ${team_schema}.gold"},
        "expected_json": {"operator": ">=", "value": 5000}, "timeout_seconds": 30,
    }],
}

_TEAM_FOR_USER = {
    "red@x.com": {"team_id": "team_red", "name": "Red", "display_name": "Red",
                  "team_catalog": "quest_pilot", "team_schema": "team_red"},
    "blue@x.com": {"team_id": "team_blue", "name": "Blue", "display_name": "Blue",
                   "team_catalog": "quest_pilot", "team_schema": "team_blue"},
}


@pytest.fixture()
def world():
    return World()


@pytest.fixture()
def client(world, monkeypatch):
    import main as m

    # Event Mode on so the GameDay endpoints are live (else they 404).
    monkeypatch.setattr(m.config, "event_mode_enabled", lambda: True)
    monkeypatch.setattr(m.config, "is_child", lambda: False)

    event = {
        "event_id": "evt_1", "slug": "pilot", "status": "active", "title": "Pilot",
        "scoring_frozen_at": None, "starts_at": None, "ends_at": None,
    }
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: event)
    monkeypatch.setattr(m.quest_packs_repo, "get_task", lambda t: _TASKS.get(t))
    monkeypatch.setattr(m.quest_packs_repo, "list_validators", lambda t: _VALIDATORS.get(t, []))

    # Real validation engine, fake warehouse: every query returns cnt=1500.
    monkeypatch.setattr(m, "default_engine", ValidationEngine(sql_executor=lambda sql, t: [{"cnt": 1500}]))

    def fake_identity(event_id, user):
        row = _TEAM_FOR_USER.get(user)
        return {"team_id": row["team_id"] if row else None, "team_row": row, "workspace_id": None}

    monkeypatch.setattr(m, "_resolve_attempt_identity", fake_identity)

    # attempts repo → in-memory
    def create_attempt(**kw):
        aid = world.next_id("att")
        world.attempts[aid] = {
            "attempt_id": aid, "event_id": kw["event_id"], "task_id": kw["task_id"],
            "team_id": kw.get("team_id"), "status": kw.get("status", "running"),
            "submitted_at": "now", "completed_at": None,
        }
        world.results[aid] = []
        return aid

    def record_result(**kw):
        world.results[kw["attempt_id"]].append({
            "validator_id": kw["validator_id"], "status": kw["status"],
            "public_message": kw.get("public_message"),
        })

    def set_status(aid, status, err=None):
        world.attempts[aid]["status"] = status
        world.attempts[aid]["completed_at"] = "now"

    monkeypatch.setattr(m.attempts_repo, "create_attempt", create_attempt)
    monkeypatch.setattr(m.attempts_repo, "record_validation_result", record_result)
    monkeypatch.setattr(m.attempts_repo, "set_status", set_status)
    monkeypatch.setattr(m.attempts_repo, "get_attempt", lambda aid: world.attempts.get(aid))
    monkeypatch.setattr(m.attempts_repo, "list_validation_results", lambda aid: world.results.get(aid, []))

    # scoring service → idempotent in-memory award
    def award(**kw):
        team_id = kw.get("team_id")
        task_id = kw.get("task_id")
        points = int(kw.get("points") or 0)
        key = (team_id, task_id)
        if not team_id or key in world.awarded:
            return {"awarded": False, "points": 0}
        world.awarded.add(key)
        world.points[team_id] = world.points.get(team_id, 0) + points
        return {"awarded": True, "points": points}

    monkeypatch.setattr(m.default_scoring_service, "award_task_base_points", award)
    monkeypatch.setattr(m, "record_audit", lambda **kw: None)
    monkeypatch.setattr(m.obs, "log_validation", lambda **kw: None)
    monkeypatch.setattr(m.obs, "log_scoring", lambda **kw: None)

    # leaderboard derives ranks from the in-memory points (desc).
    def leaderboard(eid):
        ranked = sorted(world.points.items(), key=lambda kv: -kv[1])
        rows = []
        for i, (tid, pts) in enumerate(ranked, start=1):
            rows.append({"team_id": tid, "display_name": tid.replace("team_", "").title(),
                         "total_points": pts, "rank": i})
        return rows

    monkeypatch.setattr(m.leaderboard_repo, "get_team_leaderboard", leaderboard)
    monkeypatch.setattr(m.leaderboard_repo, "recent_scoring_feed", lambda e, limit=25: [])
    monkeypatch.setattr(m.leaderboard_repo, "get_team_score", lambda e, t: world.points.get(t, 0))

    return TestClient(m.app, raise_server_exceptions=False)


def _submit(client, task_id, user):
    return client.post(
        f"/api/events/evt_1/tasks/{task_id}/attempts",
        json={"submission": {}},
        headers={"X-Forwarded-Email": user},
    )


# ── the loop ──────────────────────────────────────────────────────────────────


def test_submit_pass_awards_points(client, world):
    res = _submit(client, "task_pass", "red@x.com")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "passed"
    assert body["points_awarded"] == 100
    assert body["already_awarded"] is False
    assert world.points["team_red"] == 100


def test_resubmit_is_idempotent(client, world):
    _submit(client, "task_pass", "red@x.com")
    res = _submit(client, "task_pass", "red@x.com")
    body = res.json()
    assert body["status"] == "passed"
    assert body["points_awarded"] == 0
    assert body["already_awarded"] is True
    assert world.points["team_red"] == 100  # not double-counted


def test_submit_fail_awards_nothing(client, world):
    res = _submit(client, "task_fail", "red@x.com")
    body = res.json()
    assert body["status"] == "failed"
    assert body["points_awarded"] == 0
    assert "team_red" not in world.points


def test_two_team_leaderboard_ordering(client, world):
    # Red clears two tasks (200), Blue clears one (100) → Red ranks first.
    _submit(client, "task_pass", "red@x.com")
    _submit(client, "task_pass2", "red@x.com")
    _submit(client, "task_pass", "blue@x.com")

    res = client.get("/api/events/evt_1/leaderboard", headers={"X-Forwarded-Email": "red@x.com"})
    assert res.status_code == 200, res.text
    body = res.json()
    order = [r["team_id"] for r in body["leaderboard"]]
    assert order == ["team_red", "team_blue"]
    assert body["leaderboard"][0]["total_points"] == 200
    assert body["you"]["team_id"] == "team_red"
    assert body["you"]["rank"] == 1


def test_attempt_status_readback(client, world):
    res = _submit(client, "task_pass", "red@x.com")
    attempt_id = res.json()["attempt_id"]
    got = client.get(
        f"/api/events/evt_1/attempts/{attempt_id}",
        headers={"X-Forwarded-Email": "red@x.com"},
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["attempt"]["status"] == "passed"
    assert body["results"][0]["status"] == "passed"


def test_submit_blocked_when_event_not_active(client, world, monkeypatch):
    import main as m
    monkeypatch.setattr(
        m.events_repo, "get_event",
        lambda e: {"event_id": "evt_1", "status": "paused", "scoring_frozen_at": None},
    )
    res = _submit(client, "task_pass", "red@x.com")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "EVENT_NOT_ACTIVE"


def test_event_mode_off_makes_endpoint_404(client, monkeypatch):
    import main as m
    monkeypatch.setattr(m.config, "event_mode_enabled", lambda: False)
    res = _submit(client, "task_pass", "red@x.com")
    assert res.status_code == 404


# ── B: validator-variable namespace fallback after bootstrap ──────────────────


def test_validator_variables_fall_back_to_event_namespace(monkeypatch):
    """A team row with NULL catalog/schema still resolves to the event FQN."""
    import main as m

    event = {"event_id": "evt_1", "slug": "pilot", "config_json": None,
             "starts_at": None, "ends_at": None}
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: event)

    team_row = {"team_id": "team_red", "name": "Red",
                "team_catalog": None, "team_schema": None}  # not yet persisted
    variables = m._build_validator_variables("evt_1", team_row, "team_red")

    # Derived deterministically from event slug + team name.
    assert variables["team_catalog"] == "quest_pilot"
    assert variables["team_schema"] == "team_red"
    assert variables["team_slug"] == "red"
    assert variables["event_id"] == "evt_1"


def test_validator_variables_prefer_persisted_columns(monkeypatch):
    import main as m
    event = {"event_id": "evt_1", "slug": "pilot", "config_json": None}
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: event)

    team_row = {"team_id": "team_red", "name": "Red",
                "team_catalog": "explicit_cat", "team_schema": "explicit_schema"}
    variables = m._build_validator_variables("evt_1", team_row, "team_red")
    assert variables["team_catalog"] == "explicit_cat"
    assert variables["team_schema"] == "explicit_schema"
