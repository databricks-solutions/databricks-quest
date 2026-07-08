"""Standalone backward-compatibility guards (ADR_006).

Federation must be inert in the default standalone role: the config defaults to
standalone and write-stamping is a no-op so adoption-mode payloads are untouched.
The child path is also exercised by reloading config with a child env.
"""

import importlib


def test_default_role_is_standalone():
    import config

    importlib.reload(config)
    assert config.QUEST_ROLE == "standalone"
    assert config.is_standalone() is True
    assert config.is_child() is False
    assert config.is_master() is False


def test_invalid_role_falls_back_to_standalone(monkeypatch):
    import config

    monkeypatch.setenv("QUEST_ROLE", "not-a-real-role")
    importlib.reload(config)
    try:
        assert config.QUEST_ROLE == "standalone"
    finally:
        monkeypatch.delenv("QUEST_ROLE", raising=False)
        importlib.reload(config)


def test_stamp_is_noop_in_standalone():
    import config
    from services import federation as fed

    importlib.reload(config)
    importlib.reload(fed)
    payload = {"event_id": "evt_1", "team_id": "team_1", "points_delta": 100}
    out = fed.stamp_federated_write(payload, submitted_by="user@corp.com")
    assert "workspace_id" not in out
    assert "submitted_by" not in out
    assert out == {"event_id": "evt_1", "team_id": "team_1", "points_delta": 100}


def test_stamp_adds_workspace_for_child(monkeypatch):
    import config

    monkeypatch.setenv("QUEST_ROLE", "child")
    monkeypatch.setenv("QUEST_WORKSPACE_ID", "ws-anzgt-01")
    importlib.reload(config)
    from services import federation as fed

    importlib.reload(fed)
    try:
        payload = {"event_id": "evt_1", "points_delta": 100}
        out = fed.stamp_federated_write(payload, submitted_by="labuser+1@awsbricks.com")
        assert out["workspace_id"] == "ws-anzgt-01"
        assert out["submitted_by"] == "labuser+1@awsbricks.com"
    finally:
        monkeypatch.delenv("QUEST_ROLE", raising=False)
        monkeypatch.delenv("QUEST_WORKSPACE_ID", raising=False)
        importlib.reload(config)
        importlib.reload(fed)
