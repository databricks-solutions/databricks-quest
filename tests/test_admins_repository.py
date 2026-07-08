"""AdminsRepository — DB-backed admin allowlist write/seed logic.

Stubs the ``db`` module so no Lakebase is required: ``execute`` records the SQL
and returns a configurable rowcount, ``execute_query`` returns canned rows.
"""

import pytest

from repositories.admins import AdminsRepository, _norm


class FakeDB:
    def __init__(self, rowcount=1, rows=None):
        self.rowcount = rowcount
        self.rows = rows or []
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append(("execute", sql, params))
        return self.rowcount

    def execute_query(self, sql, params=()):
        self.calls.append(("query", sql, params))
        return self.rows


@pytest.fixture
def repo(monkeypatch):
    import repositories.admins as mod

    fake = FakeDB()
    monkeypatch.setattr(mod, "db", fake)
    r = AdminsRepository()
    r._fake = fake  # type: ignore[attr-defined]
    return r


def test_norm_lowercases_and_strips():
    assert _norm("  Alice@Corp.COM ") == "alice@corp.com"
    assert _norm(None) == ""


def test_add_normalizes_and_reports_created(repo):
    repo._fake.rowcount = 1
    assert repo.add("Alice@Corp.com", added_by="boss") is True
    kind, sql, params = repo._fake.calls[-1]
    assert kind == "execute" and "INSERT INTO quest_admins" in sql
    assert params == ("alice@corp.com", "boss", "manual")


def test_add_existing_returns_false(repo):
    repo._fake.rowcount = 0  # ON CONFLICT DO NOTHING → 0 rows
    assert repo.add("dupe@corp.com", added_by="boss") is False


def test_add_blank_email_is_noop(repo):
    assert repo.add("   ", added_by="boss") is False
    assert repo._fake.calls == []  # never touches the DB


def test_remove_normalizes(repo):
    repo._fake.rowcount = 1
    assert repo.remove("BOB@corp.com") == 1
    _, sql, params = repo._fake.calls[-1]
    assert "DELETE FROM quest_admins" in sql
    assert params == ("bob@corp.com",)


def test_seed_counts_only_new_rows(repo):
    # First insert creates (1), second is a duplicate (0).
    results = iter([1, 0])
    repo._fake.execute = lambda sql, params=(): next(results)
    added = repo.seed(["a@corp.com", "b@corp.com"])
    assert added == 1


def test_list_emails_normalizes(repo):
    repo._fake.rows = [{"email": "A@Corp.com"}, {"email": "b@corp.com"}, {"email": None}]
    assert repo.list_emails() == ["a@corp.com", "b@corp.com"]
