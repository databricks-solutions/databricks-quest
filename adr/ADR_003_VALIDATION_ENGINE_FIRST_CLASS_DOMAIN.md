# ADR 003 — Validation Engine as a First-Class Domain

## Status

Proposed

## Context

AWS GameDay-style events require teams to complete real technical objectives. System-table-based scoring is useful but insufficient for verifying specific task completion.

## Decision

Build a pluggable validation engine as a first-class service in the backend.

MVP validators:

1. SQL assertion validator
2. Databricks SDK validator
3. Manual validator

Future validators:

- system-table validator
- notebook validator
- Python code validator
- REST/API validator

## Consequences

### Positive

- Completion is objective and credible.
- Quest packs can validate real outcomes.
- Field events can handle many types of Databricks tasks.

### Negative

- Validator safety becomes a core security concern.
- Async validation workers may be required.
- More data model complexity.

## Implementation notes

- Normalize all validator results to `passed|failed|error|skipped`.
- Every validation result must tie to an attempt.
- Every score change must tie to a scoring event.
- SQL validator defaults to read-only and blocks destructive statements.
