"""Get Started tick-box on the WAREHOUSE data backend (parity with Lakebase).

Drives ``POST /api/training/attest`` through the real FastAPI stack with
``TestClient`` while the app is in **warehouse** mode. Historically the endpoint
hard-returned 409 (WAREHOUSE_READ_ONLY) on this backend, so the tick-box only
worked on Lakebase. The experience should be identical on either backend: a tick
records the course, awards points instantly, is idempotent, and derives the
Databricks Learner bonus at 2 distinct courses.

The warehouse "database" is a small in-memory fake standing in for the Delta
tables reached through the SQL warehouse (``warehouse_backend.query``). It models
the datastore semantics of the specific statements the attest path emits (MERGE =
insert-if-not-matched on the natural key, guarded SELECTs, UPDATE = set), so the
real endpoint logic — control flow, idempotency gating, Learner-bonus derivation,
points math — is exercised for real. Assertions check outcomes (status, points,
durable feed row, idempotency), never mock internals.

Run: pytest tests/test_warehouse_training_attest.py
"""

import os
import re
import sys
import types

import pytest
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "app")
for _p in (APP, REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# --------------------------------------------------------------------------- #
# In-memory fake of the warehouse (Delta) store. Models only the tables +      #
# statement shapes the attest path uses, with real insert-if-absent semantics. #
# --------------------------------------------------------------------------- #
class FakeWarehouse:
    def __init__(self):
        self.training_completions = []   # {user_id, course_id, course_type, ...}
        self.mission_completions = []    # {user_id, mission_id, points_awarded, ...}
        self.user_points_fact = []       # {user_id, mission_id, event_type, points}
        self.user_profile_snapshot = []  # {user_id, display_name, total_points}
        self.leaderboard = []            # {user_id, display_name, total_points}
        self.calls = []                  # every (sql, params)

    def query(self, sql, params=()):
        self.calls.append((sql, params))
        s = " ".join(sql.split())
        low = s.lower()
        if low.startswith("select"):
            return self._select(low, params)
        if low.startswith("merge into training_completions"):
            return self._merge_tc(params)
        if low.startswith("merge into mission_completions"):
            return self._merge_mc(params)
        if low.startswith("merge into user_points_fact"):
            return self._merge_upf(params)
        if low.startswith("update user_profile_snapshot"):
            return self._set(self.user_profile_snapshot, params[-1], int(params[0]))
        if low.startswith("insert into user_profile_snapshot"):
            self.user_profile_snapshot.append(
                {"user_id": params[0], "display_name": params[1], "total_points": int(params[2])})
            return []
        if low.startswith("update leaderboard"):
            return self._set(self.leaderboard, params[-1], int(params[0]))
        if low.startswith("insert into leaderboard"):
            self.leaderboard.append(
                {"user_id": params[0], "display_name": params[1], "total_points": int(params[2])})
            return []
        raise AssertionError(f"unexpected warehouse SQL: {s}")

    # ---- SELECTs ----
    def _select(self, low, params):
        if "from mission_completions" in low and "count(distinct mission_id)" in low:
            ids = set(re.findall(r"'([^']+)'", low))          # the inlined IN-list
            user = params[0]
            c = len({r["mission_id"] for r in self.mission_completions
                     if r["user_id"] == user and r["mission_id"] in ids})
            return [{"c": c}]
        if "from mission_completions" in low and "databricks_learner" in low:
            user = params[0]
            return [{"ok": 1}] if any(
                r["user_id"] == user and r["mission_id"] == "databricks_learner"
                for r in self.mission_completions) else []
        if "from mission_completions" in low:
            user, mission_id = params[0], params[1]
            return [{"ok": 1}] if any(
                r["user_id"] == user and r["mission_id"] == mission_id
                for r in self.mission_completions) else []
        if "from training_completions" in low:
            user, course_id = params[0], params[1]
            return [{"ok": 1}] if any(
                r["user_id"] == user and r["course_id"] == course_id
                and r["course_type"] == "self_attested" for r in self.training_completions) else []
        if "from user_profile_snapshot" in low:
            for r in self.user_profile_snapshot:
                if r["user_id"] == params[0]:
                    return [{"total_points": r["total_points"]}]
            return []
        if "from leaderboard" in low:
            for r in self.leaderboard:
                if r["user_id"] == params[0]:
                    return [{"total_points": r["total_points"]}]
            return []
        raise AssertionError(f"unexpected SELECT: {low}")

    # ---- MERGEs (WHEN NOT MATCHED THEN INSERT *) ----
    def _merge_tc(self, params):
        # params: (user_id, course_id, course_name) — completed_at is SQL-side now()
        user, course_id = params[0], params[1]
        if not any(r["user_id"] == user and r["course_id"] == course_id
                   and r["course_type"] == "self_attested" for r in self.training_completions):
            self.training_completions.append(
                {"user_id": user, "course_id": course_id, "course_name": params[2],
                 "course_type": "self_attested", "completed_at": "now"})
        return []

    def _merge_mc(self, params):
        user, mission_id = params[0], params[1]
        if not any(r["user_id"] == user and r["mission_id"] == mission_id
                   for r in self.mission_completions):
            self.mission_completions.append(
                {"user_id": user, "mission_id": mission_id, "mission_name": params[2],
                 "points_awarded": int(params[3])})
        return []

    def _merge_upf(self, params):
        user, mission_id = params[0], params[1]
        if not any(r["user_id"] == user and r["mission_id"] == mission_id
                   and r["event_type"] == "mission_completion" for r in self.user_points_fact):
            self.user_points_fact.append(
                {"user_id": user, "event_type": "mission_completion",
                 "mission_id": mission_id, "points": int(params[2])})
        return []

    @staticmethod
    def _set(table, user, new_total):
        for r in table:
            if r["user_id"] == user:
                r["total_points"] = new_total
                return []
        return []

    def total_points(self, user):
        for r in self.user_profile_snapshot:
            if r["user_id"] == user:
                return r["total_points"]
        return 0

    def self_attested_rows(self):
        return [r for r in self.training_completions if r["course_type"] == "self_attested"]


@pytest.fixture()
def wh():
    return FakeWarehouse()


@pytest.fixture()
def client(wh, monkeypatch):
    # Fake warehouse_backend BEFORE importing db/main so neither pulls in the SDK.
    # Delegate through a lambda (not the bound method) so a test can monkeypatch
    # wh.query to simulate a warehouse failure and still be seen by the app.
    fake_mod = types.ModuleType("warehouse_backend")
    fake_mod.query = lambda sql, params=(): wh.query(sql, params)
    sys.modules["warehouse_backend"] = fake_mod

    os.environ["QUEST_SQL_WAREHOUSE_ID"] = "wh123"
    os.environ["QUEST_CATALOG"] = "quest_data"
    os.environ["QUEST_SCHEMA"] = "quest"
    os.environ["QUEST_DATA_BACKEND"] = "warehouse"

    import db
    import main as m

    monkeypatch.setattr(m.db, "warehouse_backend", lambda: True)
    monkeypatch.setattr(m.db, "get_data_backend", lambda: "warehouse")
    db._backend_cache.update(value="warehouse", expiry=9e18)

    return TestClient(m.app, raise_server_exceptions=False)


def _attest(client, mission_id, user="learner@x.com"):
    return client.post(
        "/api/training/attest",
        json={"course_mission_id": mission_id},
        headers={"X-Forwarded-Email": user},
    )


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #
def test_attest_records_and_awards_on_warehouse(client, wh):
    """A tick on warehouse mode returns 200, records the course, awards 250 pts."""
    res = _attest(client, "gs_data_engineering")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    assert body["points_added"] == 250
    # exactly one durable self_attested feed row — the source of truth scoring
    # Step 2b re-derives idempotently, so the tick survives the 4-hourly rebuild
    assert len(wh.self_attested_rows()) == 1
    # instant serving award written
    assert any(r["mission_id"] == "gs_data_engineering" for r in wh.mission_completions)
    assert wh.total_points("learner@x.com") == 250


def test_attest_is_idempotent_on_warehouse(client, wh):
    """Re-ticking the same course does not double-credit points or feed rows."""
    _attest(client, "gs_data_engineering")
    res = _attest(client, "gs_data_engineering")
    assert res.status_code == 200, res.text
    assert res.json()["points_added"] == 0
    assert wh.total_points("learner@x.com") == 250
    assert len(wh.self_attested_rows()) == 1


def test_learner_bonus_fires_at_two_courses_on_warehouse(client, wh):
    """Two distinct Get Started courses earn the 500-pt Databricks Learner bonus."""
    _attest(client, "gs_data_engineering")
    res = _attest(client, "gs_machine_learning")
    assert res.status_code == 200, res.text
    awarded_ids = {a["mission_id"] for a in res.json()["awarded"]}
    assert "gs_machine_learning" in awarded_ids
    assert "databricks_learner" in awarded_ids
    assert wh.total_points("learner@x.com") == 1000  # 250 + 250 + 500


def test_attest_rejects_derived_learner_bonus_on_warehouse(client, wh):
    """The derived Learner bonus can't be self-attested directly (any backend)."""
    res = _attest(client, "databricks_learner")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "NOT_ATTESTABLE"


def test_no_warehouse_read_only_message(client, wh):
    """The old WAREHOUSE_READ_ONLY block must be gone: no 409, no Lakebase ask."""
    res = _attest(client, "gs_generative_ai")
    assert res.status_code != 409
    assert "requires the Lakebase data backend" not in res.text


def test_warehouse_write_failure_returns_graceful_503(client, wh, monkeypatch):
    """A warehouse write blowing up surfaces the same ATTEST_FAILED (503) the
    Lakebase path returns, not a raw 500 — identical failure experience."""
    def boom(sql, params=()):
        raise RuntimeError("warehouse down")
    monkeypatch.setattr(wh, "query", boom)

    res = _attest(client, "gs_data_engineering")
    assert res.status_code == 503, res.text
    assert res.json()["error"]["code"] == "ATTEST_FAILED"
