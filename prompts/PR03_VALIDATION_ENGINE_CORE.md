# PR03 Prompt — Validation Engine Core

## Branch

`feature/gameday-pr03-validation-engine`

## Goal

Implement the first-class validation engine and attempt submission path.

## Requirements

1. Add `Validator` base interface.
2. Add `SQLAssertionValidator`.
3. Add `ManualValidator`.
4. Add validation dispatch service.
5. Add safe SQL checks:
   - block destructive statements by default
   - block multiple statements by default
   - enforce timeout
   - template variables are resolved server-side
6. Add attempt submission endpoint:
   - `POST /api/events/{event_id}/tasks/{task_id}/attempts`
7. Persist attempts and validation results.
8. Add scoring event when task passes.
9. Enforce idempotency: a team can only get base points once for a task.
10. Add player-safe error messages.

## Constraints

- Do not expose raw validator exceptions to players.
- Host diagnostics can include more detail.
- Keep async validator architecture extensible, but MVP may run SQL sync.

## Suggested files

```text
app/validators/base.py
app/validators/sql_assertion.py
app/validators/manual.py
app/services/validation_engine.py
app/services/scoring_service.py
app/repositories/attempts.py
app/repositories/scoring.py
```

## Acceptance criteria

- Submitting an attempt creates a row in `task_attempts`.
- SQL validation pass creates `validation_results` and `scoring_events`.
- SQL validation fail creates failure result but no points.
- Repeat passing submission does not double-award base points.
- Manual validator returns pending/manual state.

## Verification

Create a test quest/task with SQL validator and demonstrate pass/fail using curl or documented manual steps.
