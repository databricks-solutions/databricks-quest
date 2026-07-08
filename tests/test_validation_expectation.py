"""Expectation evaluation for the SQL assertion validator (PR03)."""

import pytest

from validators.base import ValidatorConfigError
from validators.sql_assertion import evaluate_expectation


def test_default_expectation_is_at_least_one_row():
    assert evaluate_expectation(None, [{"x": 1}])["passed"] is True
    assert evaluate_expectation(None, [])["passed"] is False


def test_min_rows():
    expect = {"min_rows": 2}
    assert evaluate_expectation(expect, [{"a": 1}, {"a": 2}])["passed"] is True
    assert evaluate_expectation(expect, [{"a": 1}])["passed"] is False


@pytest.mark.parametrize(
    "operator,value,actual,expected",
    [
        (">=", 1000, 1500, True),
        (">=", 1000, 500, False),
        (">", 0, 1, True),
        ("<", 10, 5, True),
        ("<=", 5, 5, True),
        ("=", 42, 42, True),
        ("=", 42, 43, False),
        ("!=", 0, 1, True),
    ],
)
def test_numeric_operators(operator, value, actual, expected):
    result = evaluate_expectation({"operator": operator, "value": value}, [{"cnt": actual}])
    assert result["passed"] is expected


def test_string_equality_falls_back_when_not_numeric():
    assert evaluate_expectation({"operator": "=", "value": "GOLD"}, [{"s": "GOLD"}])["passed"]
    assert not evaluate_expectation({"operator": "=", "value": "GOLD"}, [{"s": "BRONZE"}])["passed"]


def test_contains_and_not_contains():
    assert evaluate_expectation({"operator": "contains", "value": "ell"}, [{"s": "hello"}])["passed"]
    assert evaluate_expectation({"operator": "not_contains", "value": "zzz"}, [{"s": "hello"}])["passed"]


def test_is_true_is_false():
    assert evaluate_expectation({"operator": "is_true"}, [{"ok": True}])["passed"]
    assert evaluate_expectation({"operator": "is_false"}, [{"ok": False}])["passed"]
    assert not evaluate_expectation({"operator": "is_true"}, [{"ok": False}])["passed"]


def test_unknown_operator_raises_config_error():
    with pytest.raises(ValidatorConfigError):
        evaluate_expectation({"operator": "~="}, [{"x": 1}])


def test_missing_operator_and_min_rows_raises():
    with pytest.raises(ValidatorConfigError):
        evaluate_expectation({"value": 1}, [{"x": 1}])


def test_numeric_operator_on_nonnumeric_raises():
    with pytest.raises(ValidatorConfigError):
        evaluate_expectation({"operator": ">=", "value": 5}, [{"s": "abc"}])
