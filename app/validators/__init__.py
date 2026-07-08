"""Validator package — the first-class validation domain (PR03).

A validator takes a normalized :class:`ValidationContext` (task config, the
player's submission, and the server-resolved template variables) and returns a
:class:`ValidationOutcome` (status + score delta + player-safe message + a
host-facing diagnostic + structured evidence).

The MVP validator set is:

- ``sql_assertion`` — run a safe, read-only SQL check and compare the result.
- ``manual``        — defer to a host; returns a ``manual`` (pending) outcome.

Execution is dispatched by :mod:`services.validation_engine`. Validators never
raise to the caller for expected failures; they return an ``error`` outcome
with a player-safe ``public_message`` and a detailed ``private_message`` so the
host can diagnose without leaking internals to players.
"""

from .base import (
    ValidationContext,
    ValidationOutcome,
    Validator,
    ValidatorConfigError,
)
from .manual import ManualValidator
from .sql_assertion import SQLAssertionValidator
from .databricks_sdk import DatabricksSDKValidator

__all__ = [
    "ValidationContext",
    "ValidationOutcome",
    "Validator",
    "ValidatorConfigError",
    "ManualValidator",
    "SQLAssertionValidator",
    "DatabricksSDKValidator",
]
