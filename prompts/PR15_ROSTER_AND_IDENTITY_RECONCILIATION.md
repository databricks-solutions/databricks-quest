# PR15 Prompt — Roster and Identity Reconciliation

## Branch

`feature/gameday-pr15-roster-reconciliation`

## Goal

Turn generic `labuser+{n}@awsbricks.com` lab users into named people and teams,
and give the host a reconciliation worklist.

## Requirements

1. Add `POST /api/host/events/{event_id}/roster/import` accepting a CSV
   (`{ "csv": "..." }`). Columns: `workspace_id` (or `workspace_host`),
   `lab_user_email`, `team_name`, optional `display_name`, `real_email`. It
   idempotently creates teams/participants/team_members and populates
   `participant_identity_map`. Return counts
   (`rows`, `teams_created`, `participants_created`, `identities_mapped`).
2. Add `GET /api/host/events/{event_id}/identities/unmapped` returning federated
   `(workspace_id, lab_user_email)` pairs writing scores not yet on the roster,
   with their unattributed point totals.
3. Add `GET /api/host/events/{event_id}/workspaces` returning per-workspace
   health (check-ins, write counts, validation pass rate, last seen).
4. Gate all three behind a master-host dependency; accept either an `event_id`
   or a slug and resolve to the canonical id.

## Constraints

- Re-import must be idempotent (match teams by `(event_id, name)`, participants
  by `(event_id, user_id)`, identity-map rows on their natural key).
- Nothing is lost: previously unmapped scores attribute on re-import.
- Validate CSV with actionable errors; never partially commit a bad import.

## Suggested files

```text
app/repositories/federation.py   # parse_roster_csv, import_roster, list_*
app/main.py                       # the three host endpoints
```

## Acceptance criteria

- Importing a roster maps identities and surfaces correct counts.
- Re-import re-attributes previously unmapped scores without duplicating
  teams/participants.
- Unmapped identities and workspace health endpoints return the expected shapes.

## Verification

- Import a sample CSV, write a federated score for an unmapped user, confirm it
  appears in `unmapped_identities`, re-import to map it, confirm attribution.
