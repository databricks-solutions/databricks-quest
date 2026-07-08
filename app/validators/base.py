"""Validator base types — the contract every validator implements.

Statuses follow the lifecycle in ``docs/06_QUEST_MODEL_AND_VALIDATION_ENGINE.md``:

- ``passed``  — the attempt satisfies the validator; points may be awarded.
- ``failed``  — the attempt was evaluated and did not satisfy the validator.
- ``error``   — the validator could not run (bad config, timeout, exec failure).
- ``skipped`` — the validator was not applicable / not enabled.
- ``manual``  — the validator defers to a host decision (pending review).

Evidence is split into a player-safe ``public_message`` and a host-only
``private_message`` (+ structured ``evidence``). Players never see raw validator
exceptions; hosts get the detail for diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# Terminal/normalized validator statuses.
PASSED = "passed"
FAILED = "failed"
ERROR = "error"
SKIPPED = "skipped"
MANUAL = "manual"

VALID_STATUSES = {PASSED, FAILED, ERROR, SKIPPED, MANUAL}


class ValidatorConfigError(Exception):
    """Raised when a validator's stored config/expectation is unusable.

    The dispatch layer converts this into an ``error`` outcome with a
    player-safe message, so a misauthored quest pack never crashes a request.
    """


@dataclass
class ValidationContext:
    """Everything a validator needs to evaluate one attempt.

    Attributes:
        validator_id: Id of the ``task_validators`` row being run.
        type: Validator type (e.g. ``sql_assertion``).
        config: Type-specific config (``config_json``), already a dict.
        expect: Optional expectation block (``expected_json``).
        timeout_seconds: Per-validator execution budget.
        submission: The player's submission payload (untrusted).
        variables: Server-resolved template variables (team_catalog, etc.).
            Only these names may appear in ``${...}`` template slots; anything
            else is rejected by the safety layer.
    """

    validator_id: str
    type: str
    config: Dict[str, Any] = field(default_factory=dict)
    expect: Optional[Dict[str, Any]] = None
    timeout_seconds: int = 30
    submission: Dict[str, Any] = field(default_factory=dict)
    variables: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationOutcome:
    """Normalized result of running one validator."""

    status: str
    score_delta: int = 0
    public_message: str = ""
    private_message: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid validator status: {self.status!r}")

    @property
    def passed(self) -> bool:
        return self.status == PASSED

    # Convenience constructors keep call sites terse and consistent.
    @classmethod
    def passed_with(
        cls,
        public_message: str,
        *,
        score_delta: int = 0,
        private_message: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> "ValidationOutcome":
        return cls(PASSED, score_delta, public_message, private_message, evidence or {})

    @classmethod
    def failed_with(
        cls,
        public_message: str,
        *,
        private_message: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> "ValidationOutcome":
        return cls(FAILED, 0, public_message, private_message, evidence or {})

    @classmethod
    def errored_with(
        cls,
        public_message: str,
        *,
        private_message: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> "ValidationOutcome":
        return cls(ERROR, 0, public_message, private_message, evidence or {})

    @classmethod
    def manual_pending(
        cls,
        public_message: str = "Submitted for host review.",
        *,
        private_message: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> "ValidationOutcome":
        return cls(MANUAL, 0, public_message, private_message, evidence or {})


class Validator:
    """Base interface. Subclasses set ``type`` and implement ``validate``."""

    type: str = ""

    def validate(self, ctx: ValidationContext) -> ValidationOutcome:
        raise NotImplementedError
