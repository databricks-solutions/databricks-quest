"""Event lifecycle state machine + participant import (PR04).

The transition rules are pure and tested directly. ``import_participants`` is
tested against a small in-memory fake of the exact statements it issues, so the
idempotency logic is verified without a live Postgres (mirrors the roster test).
"""

from contextlib import contextmanager

import pytest

from repositories.events import (
    EventsRepository,
    EventStateError,
    VALID_STATUSES,
    ALLOWED_TRANSITIONS,
    can_transition,
    attempts_open,
)


# ── Pure state-machine rules ─────────────────────────────────────────────────


def test_only_active_accepts_attempts():
    assert attempts_open("active") is True
    for s in ("draft", "ready", "paused", "frozen", "completed", "archived"):
        assert attempts_open(s) is False, s


def test_transition_table_only_references_valid_statuses():
    for src, targets in ALLOWED_TRANSITIONS.items():
        assert src in VALID_STATUSES
        for t in targets:
            assert t in VALID_STATUSES, t


def test_happy_path_transitions():
    assert can_transition("draft", "ready")
    assert can_transition("ready", "active")
    assert can_transition("active", "paused")
    assert can_transition("paused", "active")
    assert can_transition("active", "frozen")
    assert can_transition("frozen", "active")  # unfreeze
    assert can_transition("active", "completed")
    assert can_transition("completed", "archived")


def test_illegal_transitions_blocked():
    assert not can_transition("draft", "completed")
    assert not can_transition("completed", "active")
    assert not can_transition("archived", "active")
    assert not can_transition("frozen", "paused")
    assert not can_transition("active", "draft")


# ── import_participants against an in-memory fake DB ─────────────────────────


class _FakeCursor:
    def __init__(self, store):
        self.s = store
        self._last = None

    def execute(self, sql, params=()):
        up = " ".join(sql.split()).upper()
        self._last = None
        if up.startswith("SELECT TEAM_ID FROM TEAMS"):
            event_id, name = params
            tid = self.s["teams"].get((event_id, name))
            self._last = (tid,) if tid else None
        elif up.startswith("INSERT INTO TEAMS"):
            team_id, event_id, name, _display = params
            self.s["teams"].setdefault((event_id, name), team_id)
        elif up.startswith("SELECT PARTICIPANT_ID FROM PARTICIPANTS"):
            event_id, user_id = params
            pid = self.s["participants"].get((event_id, user_id))
            self._last = (pid,) if pid else None
        elif up.startswith("INSERT INTO PARTICIPANTS"):
            participant_id, event_id, user_id = params[0], params[1], params[2]
            self.s["participants"].setdefault((event_id, user_id), participant_id)
        elif up.startswith("DELETE FROM TEAM_MEMBERS"):
            # Single-team invariant: drop any membership for this participant on
            # a team other than the target before inserting (mirrors the repo).
            participant_id, _event_id, keep_team = params
            self.s["team_members"] = {
                (t, p) for (t, p) in self.s["team_members"]
                if not (p == participant_id and t != keep_team)
            }
        elif "TEAM_MEMBERS" in up and up.startswith("INSERT"):
            team_id, participant_id = params
            self.s["team_members"].add((team_id, participant_id))
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {up[:60]}")

    def fetchone(self):
        return self._last


@pytest.fixture()
def fake_db(monkeypatch):
    store = {"teams": {}, "participants": {}, "team_members": set()}
    import db

    @contextmanager
    def fake_transaction():
        yield _FakeCursor(store)

    monkeypatch.setattr(db, "transaction", fake_transaction)
    return store


def test_import_creates_teams_participants_and_assignments(fake_db):
    repo = EventsRepository()
    result = repo.import_participants(
        "evt_1",
        [
            {"email": "ada@corp.com", "display_name": "Ada", "team_name": "Red"},
            {"email": "alan@corp.com", "team_name": "Blue"},
            {"email": "grace@corp.com"},  # no team → no assignment
        ],
    )
    assert result["rows"] == 3
    assert result["participants_created"] == 3
    assert result["teams_created"] == 2
    assert result["assignments"] == 2
    assert len(fake_db["team_members"]) == 2


def test_import_is_idempotent(fake_db):
    repo = EventsRepository()
    rows = [{"email": "ada@corp.com", "team_name": "Red"}]
    repo.import_participants("evt_1", rows)
    second = repo.import_participants("evt_1", rows)
    assert second["participants_created"] == 0
    assert second["teams_created"] == 0
    assert len(fake_db["participants"]) == 1
    assert len(fake_db["teams"]) == 1


def test_import_rejects_empty_rows(fake_db):
    repo = EventsRepository()
    with pytest.raises(EventStateError):
        repo.import_participants("evt_1", [{"display_name": "no id"}])


# ── set_participant_team reassign (single team per event) ────────────────────


class _ReassignCursor:
    """Tracks team_members membership keyed by participant for reassign tests."""

    def __init__(self, store):
        self.s = store

    def execute(self, sql, params=()):
        up = " ".join(sql.split()).upper()
        if up.startswith("DELETE FROM TEAM_MEMBERS"):
            participant_id, _event_id, keep_team = params
            self.s["members"] = {
                (t, p) for (t, p) in self.s["members"]
                if not (p == participant_id and t != keep_team)
            }
        elif up.startswith("INSERT INTO TEAM_MEMBERS"):
            team_id, participant_id = params
            self.s["members"].add((team_id, participant_id))
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {up[:60]}")

    def fetchone(self):
        return None


@pytest.fixture()
def reassign_db(monkeypatch):
    store = {"members": set()}
    import db

    @contextmanager
    def fake_transaction():
        yield _ReassignCursor(store)

    monkeypatch.setattr(db, "transaction", fake_transaction)
    return store


def test_set_participant_team_moves_to_single_team(reassign_db):
    repo = EventsRepository()
    repo.set_participant_team("evt_1", "part_1", "team_red")
    assert reassign_db["members"] == {("team_red", "part_1")}
    # Reassign to a different team — old membership is removed, not duplicated.
    repo.set_participant_team("evt_1", "part_1", "team_blue")
    assert reassign_db["members"] == {("team_blue", "part_1")}


def test_set_participant_team_is_idempotent(reassign_db):
    repo = EventsRepository()
    repo.set_participant_team("evt_1", "part_1", "team_red")
    repo.set_participant_team("evt_1", "part_1", "team_red")
    assert reassign_db["members"] == {("team_red", "part_1")}
