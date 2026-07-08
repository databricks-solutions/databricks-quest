"""Validation dispatch + aggregation (PR03).

Uses an injected fake SQL executor so the engine is tested end-to-end (route →
prepare → execute → compare → normalize) without a live warehouse.
"""

from services.validation_engine import ValidationEngine, aggregate_status
from validators.base import ERROR, FAILED, MANUAL, PASSED, SKIPPED


def _sql_validator(statement, expect=None, vid="val_1"):
    return {
        "validator_id": vid,
        "type": "sql_assertion",
        "mode": "sync",
        "config_json": {"statement": statement},
        "expected_json": expect,
        "timeout_seconds": 30,
    }


def test_sql_validator_pass():
    engine = ValidationEngine(sql_executor=lambda sql, t: [{"cnt": 1500}])
    out = engine.run_validator(
        _sql_validator("SELECT COUNT(*) AS cnt FROM ${team_schema}.gold",
                       {"operator": ">=", "value": 1000}),
        submission={},
        variables={"team_schema": "gameday"},
    )
    assert out.status == PASSED
    assert out.evidence["row_count"] == 1


def test_sql_validator_fail():
    engine = ValidationEngine(sql_executor=lambda sql, t: [{"cnt": 10}])
    out = engine.run_validator(
        _sql_validator("SELECT COUNT(*) AS cnt FROM g",
                       {"operator": ">=", "value": 1000}),
        submission={},
        variables={},
    )
    assert out.status == FAILED
    # Player-safe message, no internals.
    assert "1000" not in out.public_message


def test_sql_validator_unsafe_statement_is_error_not_crash():
    engine = ValidationEngine(sql_executor=lambda sql, t: [{"x": 1}])
    out = engine.run_validator(
        _sql_validator("DROP TABLE secrets"),
        submission={},
        variables={},
    )
    assert out.status == ERROR
    assert "blocked" in (out.private_message or "").lower() or "unsafe" in (out.private_message or "").lower()


def test_sql_validator_execution_failure_is_error():
    def boom(sql, t):
        raise RuntimeError("warehouse unavailable")

    engine = ValidationEngine(sql_executor=boom)
    out = engine.run_validator(_sql_validator("SELECT 1"), {}, {})
    assert out.status == ERROR
    # The raw exception text stays in the private (host) channel only.
    assert "warehouse" in (out.private_message or "")
    assert "warehouse" not in out.public_message


def test_unresolved_template_variable_is_error():
    engine = ValidationEngine(sql_executor=lambda sql, t: [{"x": 1}])
    out = engine.run_validator(
        _sql_validator("SELECT * FROM ${team_catalog}.t"),
        submission={},
        variables={},  # team_catalog missing
    )
    assert out.status == ERROR


def test_manual_validator_is_pending():
    engine = ValidationEngine()
    out = engine.run_validator(
        {"validator_id": "v", "type": "manual", "config_json": {}, "expected_json": None,
         "timeout_seconds": 30},
        submission={},
        variables={},
    )
    assert out.status == MANUAL
    assert out.score_delta == 0


def test_unknown_validator_type_is_skipped():
    engine = ValidationEngine()
    out = engine.run_validator(
        {"validator_id": "v", "type": "telepathy", "config_json": {}, "expected_json": None,
         "timeout_seconds": 30},
        submission={},
        variables={},
    )
    assert out.status == SKIPPED


# ── aggregate_status ─────────────────────────────────────────────────────────


def _o(status):
    from validators.base import ValidationOutcome

    return ValidationOutcome(status=status, public_message="m")


def test_aggregate_all_passed():
    assert aggregate_status([_o(PASSED), _o(PASSED)]) == PASSED


def test_aggregate_any_failed():
    assert aggregate_status([_o(PASSED), _o(FAILED)]) == FAILED


def test_aggregate_error_dominates():
    assert aggregate_status([_o(PASSED), _o(ERROR), _o(FAILED)]) == ERROR


def test_aggregate_manual_pending():
    assert aggregate_status([_o(PASSED), _o(MANUAL)]) == MANUAL


def test_aggregate_empty_is_error():
    assert aggregate_status([]) == ERROR


def test_aggregate_passed_ignoring_skipped():
    assert aggregate_status([_o(PASSED), _o(SKIPPED)]) == PASSED
