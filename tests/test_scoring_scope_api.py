"""Scoring workspace scope API (Component 4: app).

Tests the helper function that parses QUEST_SCORING_WORKSPACE_IDS and the
/api/admin/pipeline-status endpoint that returns the scope.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def main_module():
    import main

    return main


@pytest.fixture
def client():
    import main

    return TestClient(main.app, raise_server_exceptions=False)


# ── get_scoring_workspace_ids() helper ───────────────────────────────────────


def test_scoring_workspace_ids_unset_returns_empty(main_module, monkeypatch):
    """When env is unset, returns empty list (whole account)."""
    monkeypatch.setenv("QUEST_SCORING_WORKSPACE_IDS", "")
    assert main_module.get_scoring_workspace_ids() == []


def test_scoring_workspace_ids_comma_separated(main_module, monkeypatch):
    """Parses comma-separated workspace IDs."""
    monkeypatch.setenv("QUEST_SCORING_WORKSPACE_IDS", "111,222")
    assert main_module.get_scoring_workspace_ids() == ["111", "222"]


def test_scoring_workspace_ids_with_whitespace(main_module, monkeypatch):
    """Handles whitespace around commas and tokens."""
    monkeypatch.setenv("QUEST_SCORING_WORKSPACE_IDS", "  111 , 222  ")
    assert main_module.get_scoring_workspace_ids() == ["111", "222"]


def test_scoring_workspace_ids_space_separated(main_module, monkeypatch):
    """Treats spaces as separators (in addition to commas)."""
    monkeypatch.setenv("QUEST_SCORING_WORKSPACE_IDS", "111 222 333")
    assert main_module.get_scoring_workspace_ids() == ["111", "222", "333"]


def test_scoring_workspace_ids_drops_non_digit_tokens(main_module, monkeypatch):
    """Drops tokens that are not all digits."""
    monkeypatch.setenv("QUEST_SCORING_WORKSPACE_IDS", "111,abc,222,0,333")
    # 0 is dropped (audit marker for account-level), abc and any non-digit is dropped
    assert main_module.get_scoring_workspace_ids() == ["111", "222", "333"]


def test_scoring_workspace_ids_mixed_separators(main_module, monkeypatch):
    """Handles mixed commas and spaces."""
    monkeypatch.setenv("QUEST_SCORING_WORKSPACE_IDS", "111, 222 , 333")
    assert main_module.get_scoring_workspace_ids() == ["111", "222", "333"]


# ── /api/admin/pipeline-status endpoint ──────────────────────────────────────


def test_pipeline_status_includes_scoring_ids_on_success(client, main_module, monkeypatch):
    """Normal response path includes scoring_workspace_ids field."""
    monkeypatch.setenv("QUEST_SCORING_WORKSPACE_IDS", "111,222")
    monkeypatch.setattr(
        main_module,
        "execute_query",
        lambda sql: (
            [{"last_run": "2026-10-12T10:00:00"}]
            if "user_profile_snapshot" in sql
            else [{"cnt": 42}]
        ),
    )

    res = client.get("/api/admin/pipeline-status")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "healthy"
    assert body["scoring_workspace_ids"] == ["111", "222"]


def test_pipeline_status_includes_scoring_ids_on_fallback(client, main_module, monkeypatch):
    """Fallback response path (when execute_query raises) includes scoring_workspace_ids."""
    monkeypatch.setenv("QUEST_SCORING_WORKSPACE_IDS", "333,444")

    def boom(sql):
        raise RuntimeError("database down")

    monkeypatch.setattr(main_module, "execute_query", boom)

    res = client.get("/api/admin/pipeline-status")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "not_initialized"
    assert body["scoring_workspace_ids"] == ["333", "444"]


def test_pipeline_status_empty_ids_when_env_unset(client, main_module, monkeypatch):
    """When QUEST_SCORING_WORKSPACE_IDS is unset, scoring_workspace_ids is empty list."""
    monkeypatch.setenv("QUEST_SCORING_WORKSPACE_IDS", "")
    monkeypatch.setattr(
        main_module,
        "execute_query",
        lambda sql: (
            [{"last_run": "2026-10-12T10:00:00"}]
            if "user_profile_snapshot" in sql
            else [{"cnt": 10}]
        ),
    )

    res = client.get("/api/admin/pipeline-status")
    assert res.status_code == 200
    body = res.json()
    assert body["scoring_workspace_ids"] == []


def test_pipeline_status_open_to_all_users(client, main_module, monkeypatch):
    """Endpoint is open to any authenticated user (uses get_user_email, not require_admin)."""
    monkeypatch.setenv("QUEST_SCORING_WORKSPACE_IDS", "111")
    monkeypatch.setattr(
        main_module,
        "execute_query",
        lambda sql: (
            [{"last_run": "2026-10-12T10:00:00"}]
            if "user_profile_snapshot" in sql
            else [{"cnt": 5}]
        ),
    )

    # Client doesn't set any user header, so it uses the default (get_user_email behavior)
    res = client.get("/api/admin/pipeline-status")
    assert res.status_code == 200
    body = res.json()
    assert "scoring_workspace_ids" in body
