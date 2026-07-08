"""Admin gating — DB-backed allowlist (quest_admins) with env bootstrap.

The effective admin set is the union of the deploy-time env allowlist
(``QUEST_ADMIN_ALLOWLIST``, the bootstrap/fallback) and the Lakebase
``quest_admins`` table (the shared source of truth). Gating is open only when
BOTH are empty. These tests stub the DB read so no Lakebase is required.
"""

import pytest
from fastapi import HTTPException


@pytest.fixture
def main_module():
    import main

    main._invalidate_admin_cache()
    return main


@pytest.fixture(autouse=True)
def _no_db_admins(main_module, monkeypatch):
    """Default: DB returns no admins. Individual tests override as needed."""
    monkeypatch.setattr(main_module.admins_repo, "list_emails", lambda: [])
    main_module._invalidate_admin_cache()
    yield
    main_module._invalidate_admin_cache()


def test_open_when_env_and_db_empty(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_ADMIN_ALLOWLIST", [])
    assert main_module.is_admin_user("anyone@corp.com") is True


def test_env_allowlist_restricts(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_ADMIN_ALLOWLIST", ["alice@corp.com"])
    assert main_module.is_admin_user("alice@corp.com") is True
    assert main_module.is_admin_user("mallory@corp.com") is False


def test_db_admins_grant_access(main_module, monkeypatch):
    # No env allowlist, but the DB lists an admin → enforced and allowed.
    monkeypatch.setattr(main_module, "QUEST_ADMIN_ALLOWLIST", [])
    monkeypatch.setattr(main_module.admins_repo, "list_emails", lambda: ["dbadmin@corp.com"])
    main_module._invalidate_admin_cache()
    assert main_module.is_admin_user("dbadmin@corp.com") is True
    assert main_module.is_admin_user("nobody@corp.com") is False


def test_union_of_env_and_db(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_ADMIN_ALLOWLIST", ["alice@corp.com"])
    monkeypatch.setattr(main_module.admins_repo, "list_emails", lambda: ["bob@corp.com"])
    main_module._invalidate_admin_cache()
    assert main_module.is_admin_user("alice@corp.com") is True  # env
    assert main_module.is_admin_user("bob@corp.com") is True  # db
    assert main_module.is_admin_user("carol@corp.com") is False


def test_db_read_failure_falls_back_to_env(main_module, monkeypatch):
    def boom():
        raise RuntimeError("lakebase down")

    monkeypatch.setattr(main_module, "QUEST_ADMIN_ALLOWLIST", ["alice@corp.com"])
    monkeypatch.setattr(main_module.admins_repo, "list_emails", boom)
    main_module._invalidate_admin_cache()
    # DB unreachable → env still gates, deployer keeps access.
    assert main_module.is_admin_user("alice@corp.com") is True
    assert main_module.is_admin_user("mallory@corp.com") is False


def test_cache_invalidation_picks_up_new_db_admin(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_ADMIN_ALLOWLIST", [])
    emails: list[str] = []
    monkeypatch.setattr(main_module.admins_repo, "list_emails", lambda: list(emails))
    main_module._invalidate_admin_cache()
    assert main_module.is_admin_user("new@corp.com") is True  # nothing configured → open
    emails.append("gatekeeper@corp.com")
    main_module._invalidate_admin_cache()
    assert main_module.is_admin_user("new@corp.com") is False  # now gated
    assert main_module.is_admin_user("gatekeeper@corp.com") is True


def test_require_admin_raises_403_for_non_admin(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_ADMIN_ALLOWLIST", ["alice@corp.com"])
    monkeypatch.setattr(main_module, "get_user_email", lambda req: "mallory@corp.com")
    with pytest.raises(HTTPException) as exc:
        main_module.require_admin(request=None)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == "FORBIDDEN"


def test_require_admin_allows_member(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_ADMIN_ALLOWLIST", ["alice@corp.com"])
    monkeypatch.setattr(main_module, "get_user_email", lambda req: "alice@corp.com")
    assert main_module.require_admin(request=None) == "alice@corp.com"
