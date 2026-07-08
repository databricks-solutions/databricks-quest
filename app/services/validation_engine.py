"""Validation dispatch — route a validator row to its implementation and run it.

Responsibilities:

- own the validator-type registry (the per-event type allowlist lives here);
- build a :class:`ValidationContext` from a stored ``task_validators`` row;
- run the validator and normalize *any* failure into a player-safe ``error``
  outcome (validators never raise to the request handler);
- aggregate per-validator outcomes into one task-level status.

The architecture is extensible (add a type to ``_REGISTRY``) and the MVP runs
SQL synchronously, as PR03 allows. Async/worker execution can later wrap the
same validators without changing call sites.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from validators import (
    DatabricksSDKValidator,
    ManualValidator,
    SQLAssertionValidator,
    ValidationContext,
    ValidationOutcome,
    ValidatorConfigError,
)
from validators.base import ERROR, FAILED, MANUAL, PASSED, SKIPPED, Validator
from validators.sql_assertion import Executor

logger = logging.getLogger("databricks-quest.services.validation_engine")

_SAFE_DEFAULT_MESSAGE = (
    "We couldn't verify this task right now. Please try again shortly."
)


class ValidationEngine:
    """Dispatches stored validator rows to validator implementations."""

    def __init__(
        self,
        sql_executor: Optional[Executor] = None,
        sdk_validator: Optional[Validator] = None,
    ):
        # ``sql_executor``/``sdk_validator`` are injectable for tests; production
        # passes None and the validators lazily build SDK-backed backends.
        sdk = sdk_validator or DatabricksSDKValidator()
        self._registry: Dict[str, Validator] = {
            "sql_assertion": SQLAssertionValidator(executor=sql_executor),
            "manual": ManualValidator(),
            "databricks_sdk": sdk,
            # ``workspace_api`` is an alias for the SDK validator (same backend);
            # both names appear across quest packs and docs.
            "workspace_api": sdk,
        }

    def supported_types(self) -> List[str]:
        return sorted(self._registry.keys())

    def run_validator(
        self,
        validator_row: Dict[str, Any],
        submission: Dict[str, Any],
        variables: Dict[str, Any],
    ) -> ValidationOutcome:
        """Run a single ``task_validators`` row and return a normalized outcome."""
        vtype = (validator_row.get("type") or "").strip()
        validator = self._registry.get(vtype)
        if validator is None:
            # Unknown/disallowed type — skip rather than fail the whole task, but
            # leave a host-facing breadcrumb.
            return ValidationOutcome(
                status=SKIPPED,
                public_message="This check isn't available yet.",
                private_message=f"no validator registered for type {vtype!r}",
                evidence={"type": vtype},
            )

        ctx = ValidationContext(
            validator_id=validator_row.get("validator_id", ""),
            type=vtype,
            config=_as_dict(validator_row.get("config_json")),
            expect=_as_dict_or_none(validator_row.get("expected_json")),
            timeout_seconds=int(validator_row.get("timeout_seconds") or 30),
            submission=submission or {},
            variables=variables or {},
        )

        try:
            return validator.validate(ctx)
        except ValidatorConfigError as exc:
            logger.warning("validator %s misconfigured: %s", ctx.validator_id, exc)
            return ValidationOutcome.errored_with(
                _SAFE_DEFAULT_MESSAGE,
                private_message=f"config error: {exc}",
                evidence={"type": vtype, "stage": "config"},
            )
        except Exception as exc:  # noqa: BLE001 - never leak internals to players
            logger.exception("validator %s crashed", ctx.validator_id)
            return ValidationOutcome.errored_with(
                _SAFE_DEFAULT_MESSAGE,
                private_message=f"unexpected error: {type(exc).__name__}: {exc}",
                evidence={"type": vtype, "stage": "dispatch"},
            )


def aggregate_status(outcomes: List[ValidationOutcome]) -> str:
    """Reduce per-validator outcomes to a single task-level status.

    Rules (MVP):
    - no outcomes → ``error`` (a task must declare at least one validator);
    - any ``error`` → ``error``;
    - any ``failed`` → ``failed``;
    - any ``manual`` (and no fail/error) → ``manual`` (pending host review);
    - all non-skipped outcomes passed → ``passed``;
    - only skipped → ``skipped``.
    """
    if not outcomes:
        return ERROR
    statuses = [o.status for o in outcomes]
    if ERROR in statuses:
        return ERROR
    if FAILED in statuses:
        return FAILED
    if MANUAL in statuses:
        return MANUAL
    non_skipped = [s for s in statuses if s != SKIPPED]
    if non_skipped and all(s == PASSED for s in non_skipped):
        return PASSED
    return SKIPPED


def _as_dict(value: Any) -> Dict[str, Any]:
    out = _as_dict_or_none(value)
    return out if out is not None else {}


def _as_dict_or_none(value: Any) -> Optional[Dict[str, Any]]:
    """Coerce a JSON column (already-parsed dict or JSON string) to a dict."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            return None
    return None


# Module-level default engine for request handlers (production path).
default_engine = ValidationEngine()
