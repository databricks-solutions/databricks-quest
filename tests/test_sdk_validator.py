"""Executable ``databricks_sdk`` validator + ``sdk_checks`` registry (PR-pilot C).

DB- and SDK-free: a fake duck-typed WorkspaceClient is injected so the checks
run their real lookup logic without the SDK installed. Covers the four outcome
classes the validator must produce:

- artefact found            → ``passed``
- artefact not found        → ``failed``
- unknown check / bad param → ``error`` (authoring bug, via ValidatorConfigError)
- check can't run at runtime → ``manual`` (host-review fallback, never a block)
"""

import pytest

from services.sdk_checks import (
    SDK_CHECKS,
    SDKCheckConfigError,
    dashboard_exists_for_team,
    known_checks,
    table_exists,
)
from validators.base import ERROR, FAILED, MANUAL, PASSED, ValidationContext
from validators.databricks_sdk import DatabricksSDKValidator


# ── fake workspace client ─────────────────────────────────────────────────────


class _Named:
    def __init__(self, name, **extra):
        self.display_name = name
        for k, v in extra.items():
            setattr(self, k, v)


class _Lakeview:
    def __init__(self, names):
        self._names = names

    def list(self):
        return [_Named(n) for n in self._names]


class _Tables:
    def __init__(self, present):
        self._present = set(present)

    def get(self, full_name):
        if full_name not in self._present:
            raise RuntimeError("NOT_FOUND")
        return _Named(full_name)


class FakeClient:
    """Duck-typed WorkspaceClient with just the surfaces the checks probe."""

    def __init__(self, dashboards=None, tables=None):
        if dashboards is not None:
            self.lakeview = _Lakeview(dashboards)
        if tables is not None:
            self.tables = _Tables(tables)


def _validator(client=None, *, raise_client=None):
    def factory():
        if raise_client is not None:
            raise raise_client
        return client

    return DatabricksSDKValidator(client_factory=factory)


def _ctx(check, params=None, variables=None):
    return ValidationContext(
        validator_id="v1",
        type="databricks_sdk",
        config={"check": check, "params": params or {}},
        variables=variables or {},
    )


# ── outcome classes ───────────────────────────────────────────────────────────


def test_passed_when_artifact_found():
    v = _validator(FakeClient(dashboards=["Team Red Sales", "Other"]))
    out = v.validate(_ctx("dashboard_exists_for_team", {"name_contains": "Team Red"}))
    assert out.status == PASSED
    assert out.evidence["check"] == "dashboard_exists_for_team"


def test_failed_when_artifact_absent():
    v = _validator(FakeClient(dashboards=["Other dashboard"]))
    out = v.validate(_ctx("dashboard_exists_for_team", {"name_contains": "Team Red"}))
    assert out.status == FAILED
    # Player-safe message, no internals.
    assert "workspace" in out.public_message.lower()


def test_unknown_check_is_config_error():
    from validators.base import ValidatorConfigError

    v = _validator(FakeClient(dashboards=[]))
    with pytest.raises(ValidatorConfigError):
        v.validate(_ctx("does_not_exist"))


def test_missing_check_name_is_config_error():
    from validators.base import ValidatorConfigError

    v = _validator(FakeClient(dashboards=[]))
    with pytest.raises(ValidatorConfigError):
        v.validate(_ctx("", {}))


def test_client_unavailable_routes_to_manual():
    v = _validator(raise_client=RuntimeError("no creds"))
    out = v.validate(_ctx("dashboard_exists_for_team", {"name_contains": "x"}))
    assert out.status == MANUAL
    assert out.evidence["stage"] == "client"
    # Raw exception text stays host-only.
    assert "no creds" in (out.private_message or "")
    assert "no creds" not in out.public_message


def test_runtime_check_failure_routes_to_manual():
    # A client with no lakeview/dashboards surface → _list_dashboards raises at
    # runtime → host review, not a hard error.
    v = _validator(FakeClient())  # no dashboards/tables attrs
    out = v.validate(_ctx("dashboard_exists_for_team", {"name_contains": "x"}))
    assert out.status == MANUAL
    assert out.evidence["stage"] == "execute"


def test_required_param_missing_is_config_error():
    from validators.base import ValidatorConfigError

    v = _validator(FakeClient(tables=[]))
    with pytest.raises(ValidatorConfigError):
        v.validate(_ctx("table_exists", {}))  # 'table' param required


# ── the engine maps a ValidatorConfigError to a host-visible error ────────────


def test_engine_maps_config_error_to_error_status():
    from services.validation_engine import ValidationEngine

    engine = ValidationEngine(sdk_validator=_validator(FakeClient(dashboards=[])))
    out = engine.run_validator(
        {
            "validator_id": "v1",
            "type": "databricks_sdk",
            "config_json": {"check": "nope"},
            "expected_json": None,
            "timeout_seconds": 30,
        },
        submission={},
        variables={},
    )
    assert out.status == ERROR


# ── template-variable resolution in params ────────────────────────────────────


def test_param_slots_resolved_from_variables():
    captured = {}

    def fake_check(client, params, ctx):
        captured.update(params)
        return {"found": True, "detail": "ok", "evidence": {}}

    v = DatabricksSDKValidator(
        client_factory=lambda: FakeClient(dashboards=[]),
        checks={"probe": fake_check},
    )
    out = v.validate(
        _ctx("probe", {"name_contains": "${team_slug}"}, {"team_slug": "team_red"})
    )
    assert out.status == PASSED
    assert captured["name_contains"] == "team_red"


def test_unresolved_optional_param_is_dropped():
    captured = {}

    def fake_check(client, params, ctx):
        captured.update(params)
        return {"found": True, "detail": "ok", "evidence": {}}

    v = DatabricksSDKValidator(
        client_factory=lambda: FakeClient(dashboards=[]),
        checks={"probe": fake_check},
    )
    # ${event_start} not provided → the filter is dropped, not failed.
    v.validate(_ctx("probe", {"created_after": "${event_start}", "keep": "yes"}))
    assert "created_after" not in captured
    assert captured["keep"] == "yes"


# ── sdk_checks registry directly ──────────────────────────────────────────────


def test_table_exists_found_and_missing():
    client = FakeClient(tables=["main.gameday.gold"])
    assert table_exists(client, {"table": "main.gameday.gold"}, None)["found"] is True
    assert table_exists(client, {"table": "main.gameday.missing"}, None)["found"] is False


def test_table_exists_requires_table_param():
    with pytest.raises(SDKCheckConfigError):
        table_exists(FakeClient(tables=[]), {}, None)


def test_dashboard_check_name_filter():
    client = FakeClient(dashboards=["Red KPI", "Blue KPI"])
    assert dashboard_exists_for_team(client, {"name_contains": "Red"}, None)["found"]
    assert not dashboard_exists_for_team(client, {"name_contains": "Green"}, None)["found"]


def test_known_checks_lists_registry():
    names = known_checks()
    assert "dashboard_exists_for_team" in names
    assert "table_exists" in names
    assert set(names) == set(SDK_CHECKS.keys())
