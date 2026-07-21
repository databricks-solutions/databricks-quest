# PR10 Prompt — Security, Observability, and Audit

## Branch

`feature/gameday-pr10-security-observability`

## Goal

Harden the product for credible field usage.

## Requirements

1. Enforce role checks across host APIs.
2. Add event/team scope checks for player APIs.
3. Harden SQL validator allowlist/denylist.
4. Add audit events for all mutations.
5. Add structured logging for validation and scoring.
6. Add request IDs to errors.
7. Add health indicators for Lakebase, migrations, validators, and scoring.
8. Add documentation for permission model.

## Acceptance criteria

- Player cannot call host APIs.
- Player cannot submit against another team namespace.
- Destructive SQL is blocked by default.
- All mutations write audit log.
- Validation errors are player-safe.
- Host can see diagnostics.

## Verification

Manual negative tests for role and SQL safety.
