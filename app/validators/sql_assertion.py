"""SQL assertion validator.

Runs a single safe, read-only query against a Databricks SQL warehouse and
compares the result to an ``expect`` block. The execution backend is injected
(``executor``) so the safety/templating/comparison logic can be unit-tested
without a live warehouse; in production the default executor uses the Databricks
SDK Statement Execution API.

Config (``config_json``)::

    statement: "SELECT COUNT(*) AS cnt FROM ${team_catalog}.${team_schema}.gold"
    warehouse_id: "<optional; falls back to QUEST_SQL_WAREHOUSE_ID>"

Expect (``expected_json``)::

    operator: ">="          # = != > >= < <= contains not_contains is_true is_false
    value: 1000
    # or:
    min_rows: 1             # pass when the query returns at least N rows

When no ``expect`` is given the default assertion is "returns at least one row".
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from .base import ValidationContext, ValidationOutcome, Validator, ValidatorConfigError
from .safety import UnsafeSQLError, prepare_statement

logger = logging.getLogger("databricks-quest.validators.sql_assertion")

# An executor takes a prepared (already-safe) SQL string + timeout and returns
# rows as a list of column-ordered dicts.
Executor = Callable[[str, int], List[Dict[str, Any]]]

# Evidence row/char caps so a validator can never persist a large payload.
_MAX_EVIDENCE_ROWS = 5
_MAX_EVIDENCE_CHARS = 2000

_NUMERIC_OPERATORS = {"=", "!=", ">", ">=", "<", "<="}
_KNOWN_OPERATORS = _NUMERIC_OPERATORS | {
    "contains",
    "not_contains",
    "is_true",
    "is_false",
}


def _first_scalar(rows: List[Dict[str, Any]]) -> Any:
    """Return the first column of the first row, or None when empty."""
    if not rows:
        return None
    first = rows[0]
    if not first:
        return None
    return next(iter(first.values()))


def _coerce_number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_expectation(
    expect: Optional[Dict[str, Any]], rows: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Compare query ``rows`` against the ``expect`` block.

    Returns ``{"passed": bool, "reason": str, "actual": Any}``. Raises
    :class:`ValidatorConfigError` if the expectation itself is malformed.
    """
    row_count = len(rows)

    # No expectation → "at least one row".
    if not expect:
        return {
            "passed": row_count >= 1,
            "reason": f"returned {row_count} row(s); expected at least 1",
            "actual": row_count,
        }

    # Row-count assertion.
    if expect.get("min_rows") is not None:
        try:
            min_rows = int(expect["min_rows"])
        except (TypeError, ValueError):
            raise ValidatorConfigError("expect.min_rows must be an integer")
        return {
            "passed": row_count >= min_rows,
            "reason": f"returned {row_count} row(s); expected >= {min_rows}",
            "actual": row_count,
        }

    operator = expect.get("operator")
    if operator is None:
        raise ValidatorConfigError("expect requires 'operator' or 'min_rows'")
    if operator not in _KNOWN_OPERATORS:
        raise ValidatorConfigError(f"unsupported expect operator: {operator!r}")

    actual = _first_scalar(rows)

    if operator in ("is_true", "is_false"):
        truthy = bool(actual)
        passed = truthy if operator == "is_true" else not truthy
        return {
            "passed": passed,
            "reason": f"value is {actual!r}; expected {operator}",
            "actual": actual,
        }

    expected = expect.get("value")

    if operator in ("contains", "not_contains"):
        hay = "" if actual is None else str(actual)
        needle = "" if expected is None else str(expected)
        found = needle in hay
        passed = found if operator == "contains" else not found
        return {
            "passed": passed,
            "reason": f"value {actual!r} {operator} {expected!r}",
            "actual": actual,
        }

    # Numeric / equality operators.
    if operator in ("=", "!="):
        # Prefer numeric comparison, fall back to string equality.
        a_num, e_num = _coerce_number(actual), _coerce_number(expected)
        if a_num is not None and e_num is not None:
            equal = a_num == e_num
        else:
            equal = str(actual) == str(expected)
        passed = equal if operator == "=" else not equal
        return {
            "passed": passed,
            "reason": f"value {actual!r} {operator} {expected!r}",
            "actual": actual,
        }

    a_num, e_num = _coerce_number(actual), _coerce_number(expected)
    if a_num is None or e_num is None:
        raise ValidatorConfigError(
            f"operator {operator!r} requires numeric value and result"
        )
    comparisons = {
        ">": a_num > e_num,
        ">=": a_num >= e_num,
        "<": a_num < e_num,
        "<=": a_num <= e_num,
    }
    return {
        "passed": comparisons[operator],
        "reason": f"value {a_num} {operator} {e_num}",
        "actual": actual,
    }


def _truncate_evidence(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cap evidence rows and total size so nothing large is persisted."""
    sample = rows[:_MAX_EVIDENCE_ROWS]
    out: List[Dict[str, Any]] = []
    budget = _MAX_EVIDENCE_CHARS
    for row in sample:
        trimmed = {k: (str(v)[:200]) for k, v in row.items()}
        size = sum(len(k) + len(v) for k, v in trimmed.items())
        if size > budget:
            break
        budget -= size
        out.append(trimmed)
    return out


class SQLAssertionValidator(Validator):
    """Validator that runs a safe SELECT and checks the result."""

    type = "sql_assertion"

    def __init__(self, executor: Optional[Executor] = None):
        # Lazily create the SDK-backed executor so unit tests (which inject one)
        # never need the Databricks SDK installed.
        self._executor = executor

    def _resolve_executor(self, ctx: ValidationContext) -> Executor:
        if self._executor is not None:
            return self._executor
        from services.sql_runner import build_warehouse_executor

        warehouse_id = ctx.config.get("warehouse_id")
        return build_warehouse_executor(warehouse_id)

    def validate(self, ctx: ValidationContext) -> ValidationOutcome:
        safe_message = (
            ctx.config.get("safe_error_message")
            or "We couldn't verify this task. Check the instructions and try again."
        )

        statement = ctx.config.get("statement") or ctx.config.get("sql")
        if not statement:
            raise ValidatorConfigError("sql_assertion requires a 'statement'")

        try:
            prepared = prepare_statement(statement, ctx.variables)
        except UnsafeSQLError as exc:
            # Authoring/templating problem — host-visible, player-safe.
            return ValidationOutcome.errored_with(
                safe_message,
                private_message=f"unsafe SQL rejected: {exc}",
                evidence={"stage": "prepare"},
            )

        try:
            executor = self._resolve_executor(ctx)
            rows = executor(prepared, ctx.timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - never leak internals to players
            logger.warning("sql_assertion execution failed: %s", exc)
            return ValidationOutcome.errored_with(
                safe_message,
                private_message=f"execution error: {type(exc).__name__}: {exc}",
                evidence={"stage": "execute"},
            )

        result = evaluate_expectation(ctx.expect, rows)
        evidence = {
            "row_count": len(rows),
            "rows": _truncate_evidence(rows),
            "assertion": result["reason"],
        }
        if result["passed"]:
            return ValidationOutcome.passed_with(
                "Validated successfully.",
                private_message=result["reason"],
                evidence=evidence,
            )
        return ValidationOutcome.failed_with(
            ctx.config.get("fail_message")
            or "Not quite — the check did not pass yet. Review the criteria and retry.",
            private_message=result["reason"],
            evidence=evidence,
        )
