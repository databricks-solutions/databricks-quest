"""Databricks SDK validator — execute a read-only workspace lookup.

Reads ``ctx.config["check"]`` (the check name) and ``ctx.config["params"]``
(check arguments, with ``${...}`` slots resolved from the server-provided
``ctx.variables``), then dispatches to a check in
:mod:`services.sdk_checks`. Checks are lookups only — they never create or
mutate workspace state.

Outcome mapping:

- artefact found        → ``passed``
- artefact not found    → ``failed``
- unknown/missing check or required param → :class:`ValidatorConfigError`
  (authoring bug; the engine turns this into a host-visible ``error``)
- the check could not run (SDK unavailable, API error) → ``manual`` so the
  task routes to host review and a pilot is never hard-blocked.

Why ``manual`` (not ``skipped``) on a can't-run: ``aggregate_status`` ranks
``MANUAL`` above ``PASSED`` but a lone ``SKIPPED`` task never completes and has
no review path. Returning ``manual`` from this single validator therefore both
(a) preserves auto-grading when the check *can* run and (b) gives a host-review
fallback when it can't — without pairing a redundant always-``manual`` validator
(which would suppress auto-grading on a passing check).

The ``workspace_api`` validator type is an alias for this validator.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, Optional

from .base import (
    ValidationContext,
    ValidationOutcome,
    Validator,
    ValidatorConfigError,
)

logger = logging.getLogger("databricks-quest.validators.databricks_sdk")

_SLOT_RE = re.compile(r"\$\{([A-Za-z0-9_.]+)\}")


class _UnresolvedVariable(Exception):
    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


def _resolve_params(params: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve ``${name}`` slots in string params from ``variables``.

    A param whose template references a variable the server did not provide is
    **dropped** (treated as "filter not supplied") rather than failing the
    check, so an optional filter like ``created_after: ${event_start}`` simply
    does not constrain the lookup when that context is unavailable.
    """
    resolved: Dict[str, Any] = {}
    for key, value in (params or {}).items():
        if isinstance(value, str) and "${" in value:
            try:
                resolved[key] = _SLOT_RE.sub(lambda m: _sub(m, variables), value)
            except _UnresolvedVariable:
                continue
        else:
            resolved[key] = value
    return resolved


def _sub(match: "re.Match[str]", variables: Dict[str, Any]) -> str:
    name = match.group(1)
    if name in variables and variables[name] is not None:
        return str(variables[name])
    raise _UnresolvedVariable(name)


class DatabricksSDKValidator(Validator):
    """Validator that runs a read-only Databricks workspace check via the SDK."""

    type = "databricks_sdk"

    def __init__(
        self,
        client_factory: Optional[Callable[[], Any]] = None,
        checks: Optional[Dict[str, Any]] = None,
    ):
        # ``client_factory`` and ``checks`` are injectable for tests; production
        # lazily builds a WorkspaceClient and uses the default check registry.
        self._client_factory = client_factory
        self._checks = checks

    def _registry(self) -> Dict[str, Any]:
        if self._checks is not None:
            return self._checks
        from services.sdk_checks import SDK_CHECKS

        return SDK_CHECKS

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient()

    def validate(self, ctx: ValidationContext) -> ValidationOutcome:
        from services.sdk_checks import SDKCheckConfigError

        check_name = (ctx.config.get("check") or "").strip()
        if not check_name:
            raise ValidatorConfigError("databricks_sdk requires a 'check'")
        check = self._registry().get(check_name)
        if check is None:
            raise ValidatorConfigError(f"unknown databricks_sdk check: {check_name!r}")

        params = _resolve_params(ctx.config.get("params") or {}, ctx.variables)

        # Build the client. Failure here means the check can't run → route to
        # host review (manual), never an error and never a dead-end skip.
        try:
            client = self._client()
        except Exception as exc:  # noqa: BLE001
            logger.warning("databricks_sdk client unavailable: %s", exc)
            return ValidationOutcome.manual_pending(
                "This check will be confirmed by your host.",
                private_message=f"workspace client unavailable: {type(exc).__name__}: {exc}",
                evidence={"check": check_name, "stage": "client"},
            )

        try:
            result = check(client, params, ctx)
        except SDKCheckConfigError as exc:
            # Authoring problem — surface as a config error (host-visible).
            raise ValidatorConfigError(str(exc))
        except Exception as exc:  # noqa: BLE001 - runtime inability → host review
            logger.warning("databricks_sdk check %s failed to run: %s", check_name, exc)
            return ValidationOutcome.manual_pending(
                "This check will be confirmed by your host.",
                private_message=f"check {check_name!r} could not run: {type(exc).__name__}: {exc}",
                evidence={"check": check_name, "stage": "execute"},
            )

        evidence = {"check": check_name, **(result.get("evidence") or {})}
        detail = result.get("detail") or check_name
        if result.get("found"):
            return ValidationOutcome.passed_with(
                "Verified in your workspace.",
                private_message=detail,
                evidence=evidence,
            )
        return ValidationOutcome.failed_with(
            "Not detected yet — complete the task in your workspace and resubmit.",
            private_message=detail,
            evidence=evidence,
        )
