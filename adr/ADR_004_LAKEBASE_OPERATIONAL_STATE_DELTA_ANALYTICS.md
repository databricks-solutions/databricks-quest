# ADR 004 — Lakebase for Operational State, Delta for Analytics

## Status

Proposed

## Context

The current app uses Delta tables as scored state and Lakebase as the fast read model. For GameDay, participants submit validations and expect immediate feedback and leaderboard changes.

## Decision

Use Lakebase as the operational store for GameDay state. Use Delta/Unity Catalog for audit, analytics, and telemetry joins.

## Consequences

### Positive

- Fast reads/writes for attempts, scores, and leaderboard.
- Transactional scoring/idempotency is simpler.
- App responsiveness improves.
- Delta still supports analytics and historical reporting.

### Negative

- Need a reliable Lakebase-to-Delta sync path.
- Need migrations for Lakebase.
- Need clarity on source of truth per table.

## Implementation notes

- Operational tables: events, teams, participants, attempts, validation results, scoring events.
- Analytics tables: append-only copies/snapshots in Delta.
- Existing adoption scoring can stay Delta-first initially.
