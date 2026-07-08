"""Manual validator — defers the decision to a host.

Returns a ``manual`` (pending) outcome and never awards points on its own. A
host later records a manual scoring adjustment (a separate scoring event, per
``docs/06`` "Idempotency"), which keeps the original attempt/result immutable.
"""

from __future__ import annotations

from .base import ValidationContext, ValidationOutcome, Validator


class ManualValidator(Validator):
    type = "manual"

    def validate(self, ctx: ValidationContext) -> ValidationOutcome:
        public = (
            ctx.config.get("pending_message")
            or "Submitted for host review. A facilitator will confirm this task."
        )
        return ValidationOutcome.manual_pending(
            public,
            private_message="manual validator: awaiting host decision",
            evidence={"requires_host_review": True},
        )
