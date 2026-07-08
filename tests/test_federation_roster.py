"""Roster parsing + idempotent import / re-attribution (ADR_006).

``parse_roster_csv`` is pure and tested directly. ``import_roster`` is tested
against a small in-memory fake of the exact statements it issues, so the
idempotency logic (match teams by (event, name), participants by (event,
user_id), upsert identity map) is verified without a live Postgres.
"""

from contextlib import contextmanager

import pytest

from repositories.federation import FederationRepository, RosterImportError


# ── parse_roster_csv ─────────────────────────────────────────────────────────


def test_parse_basic_roster():
    csv_text = (
        "workspace_id,lab_user_email,display_name,real_email,team_name\n"
        "ws-01,labuser+1@awsbricks.com,Ada Lovelace,ada@corp.com,Red Team\n"
        "ws-02,labuser+2@awsbricks.com,Alan Turing,alan@corp.com,Blue Team\n"
    )
    rows = FederationRepository.parse_roster_csv(csv_text)
    assert len(rows) == 2
    assert rows[0]["workspace_id"] == "ws-01"
    assert rows[0]["lab_user_email"] == "labuser+1@awsbricks.com"
    assert rows[0]["team_name"] == "Red Team"


def test_header_aliases_and_host_fallback():
    csv_text = (
        "workspace_host,email,name,team\n"
        "https://ws-01.databricks.com,labuser+1@awsbricks.com,Ada,Red Team\n"
    )
    rows = FederationRepository.parse_roster_csv(csv_text)
    assert len(rows) == 1
    # workspace_id falls back to the host when only a host column is given.
    assert rows[0]["workspace_id"] == "https://ws-01.databricks.com"
    assert rows[0]["display_name"] == "Ada"


def test_partial_rows_are_skipped_not_fatal():
    csv_text = (
        "workspace_id,lab_user_email,team_name\n"
        "ws-01,labuser+1@awsbricks.com,Red Team\n"
        ",,\n"
        "ws-02,labuser+2@awsbricks.com,Blue Team\n"
    )
    rows = FederationRepository.parse_roster_csv(csv_text)
    assert len(rows) == 2


def test_missing_required_columns_raise():
    with pytest.raises(RosterImportError):
        FederationRepository.parse_roster_csv("workspace_id,team_name\nws-01,Red\n")
    with pytest.raises(RosterImportError):
        FederationRepository.parse_roster_csv("")
    with pytest.raises(RosterImportError):
        FederationRepository.parse_roster_csv(
            "lab_user_email,team_name\nlabuser+1@awsbricks.com,Red\n"
        )  # no workspace column


# ── import_roster against an in-memory fake DB ───────────────────────────────


class _FakeCursor:
    """Interprets the fixed set of statements import_roster issues."""

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
        elif up.startswith("UPDATE PARTICIPANTS"):
            pass  # field updates are immaterial to the idempotency assertions
        elif up.startswith("INSERT INTO PARTICIPANTS"):
            participant_id, event_id, user_id = params[0], params[1], params[2]
            self.s["participants"].setdefault((event_id, user_id), participant_id)
        elif "TEAM_MEMBERS" in up and up.startswith("INSERT"):
            team_id, participant_id = params
            self.s["team_members"].add((team_id, participant_id))
        elif "PARTICIPANT_IDENTITY_MAP" in up and up.startswith("INSERT"):
            (event_id, workspace_id, lab_user, participant_id, team_id,
             real_email, display_name) = params
            self.s["identity_map"][(event_id, workspace_id, lab_user)] = {
                "participant_id": participant_id,
                "team_id": team_id,
                "real_email": real_email,
                "display_name": display_name,
            }
        else:  # pragma: no cover - guards against an unhandled statement
            raise AssertionError(f"unexpected SQL: {up[:60]}")

    def fetchone(self):
        return self._last


@pytest.fixture()
def fake_db(monkeypatch):
    store = {
        "teams": {},
        "participants": {},
        "team_members": set(),
        "identity_map": {},
    }
    import db

    @contextmanager
    def fake_transaction():
        yield _FakeCursor(store)

    monkeypatch.setattr(db, "transaction", fake_transaction)
    return store


def test_import_then_reimport_is_idempotent(fake_db):
    repo = FederationRepository()
    csv_text = (
        "workspace_id,lab_user_email,display_name,real_email,team_name\n"
        "ws-01,labuser+1@awsbricks.com,Ada,ada@corp.com,Red Team\n"
        "ws-02,labuser+2@awsbricks.com,Alan,alan@corp.com,Blue Team\n"
    )

    first = repo.import_roster("evt_1", csv_text)
    assert first["rows"] == 2
    assert first["teams_created"] == 2
    assert first["participants_created"] == 2
    assert first["identities_mapped"] == 2
    assert len(fake_db["teams"]) == 2
    assert len(fake_db["participants"]) == 2
    assert len(fake_db["identity_map"]) == 2

    second = repo.import_roster("evt_1", csv_text)
    assert second["rows"] == 2
    # Nothing new is created on a re-import.
    assert second["teams_created"] == 0
    assert second["participants_created"] == 0
    assert len(fake_db["teams"]) == 2
    assert len(fake_db["participants"]) == 2
    assert len(fake_db["identity_map"]) == 2


def test_reimport_reattributes_changed_team(fake_db):
    """Moving a user to a new team on re-import updates their identity mapping.

    This is the mechanism by which previously unmapped/mis-mapped scores get
    re-attributed: the (event, workspace, labuser) key upserts to the new team.
    """
    repo = FederationRepository()
    repo.import_roster(
        "evt_1",
        "workspace_id,lab_user_email,team_name\n"
        "ws-01,labuser+1@awsbricks.com,Red Team\n",
    )
    key = ("evt_1", "ws-01", "labuser+1@awsbricks.com")
    red_team = fake_db["identity_map"][key]["team_id"]

    # Re-import with the same user assigned to a different team.
    repo.import_roster(
        "evt_1",
        "workspace_id,lab_user_email,team_name\n"
        "ws-01,labuser+1@awsbricks.com,Blue Team\n",
    )
    blue_team = fake_db["identity_map"][key]["team_id"]
    assert blue_team != red_team
    # Still exactly one mapping row for that identity (upsert, not duplicate).
    assert len([k for k in fake_db["identity_map"] if k == key]) == 1
