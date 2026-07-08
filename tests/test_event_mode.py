"""Event Mode gating — GameDay is opt-in; legacy adoption is the default.

These exercise the single resolution point (``config``) that every gate in the
app keys off (``require_event_mode``/``require_host`` raise 404 when this is
False, and the frontend hides the Event nav).
"""

import importlib

import config as config_module


def _reload(monkeypatch, env):
    for key in ("QUEST_ROLE", "QUEST_EVENT_MODE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(config_module)


def test_default_deploy_is_legacy(monkeypatch):
    c = _reload(monkeypatch, {})
    assert c.QUEST_ROLE == "standalone"
    assert c.event_mode_enabled() is False


def test_explicit_truthy_values_enable(monkeypatch):
    for value in ("on", "ON", "true", "1", "yes", "enabled", "enable"):
        c = _reload(monkeypatch, {"QUEST_EVENT_MODE": value})
        assert c.event_mode_enabled() is True, value


def test_blank_or_falsy_values_stay_legacy(monkeypatch):
    for value in ("", "off", "false", "0", "no", "disabled", "nope"):
        c = _reload(monkeypatch, {"QUEST_EVENT_MODE": value})
        assert c.event_mode_enabled() is False, value


def test_master_and_child_roles_imply_event_mode(monkeypatch):
    for role in ("master", "child"):
        c = _reload(monkeypatch, {"QUEST_ROLE": role})
        assert c.event_mode_enabled() is True, role


def test_role_implication_overrides_explicit_off(monkeypatch):
    # A federated deploy is inherently an event — can't be turned off.
    c = _reload(monkeypatch, {"QUEST_ROLE": "master", "QUEST_EVENT_MODE": "off"})
    assert c.event_mode_enabled() is True


def test_standalone_with_event_mode_on(monkeypatch):
    # Single-workspace GameDay: role stays standalone, Event Mode on.
    c = _reload(monkeypatch, {"QUEST_ROLE": "standalone", "QUEST_EVENT_MODE": "on"})
    assert c.QUEST_ROLE == "standalone"
    assert c.event_mode_enabled() is True


def test_summary_reports_event_mode(monkeypatch):
    c = _reload(monkeypatch, {"QUEST_EVENT_MODE": "on"})
    assert c.summary()["event_mode"] is True
    c = _reload(monkeypatch, {})
    assert c.summary()["event_mode"] is False


def teardown_module(module):  # noqa: D401 - restore default import state
    """Reload config with a clean env so later tests see defaults."""
    import os

    os.environ.pop("QUEST_ROLE", None)
    os.environ.pop("QUEST_EVENT_MODE", None)
    importlib.reload(config_module)
