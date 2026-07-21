# PR14 Prompt — Child Wiring and Shared Writer Credential

## Branch

`feature/gameday-pr14-child-wiring`

## Goal

Let a child workspace write to the master Lakebase safely and idempotently, and
provision the restricted shared credential it uses.

## Requirements

1. Stamp federated writes with `QUEST_WORKSPACE_ID` and `submitted_by` (the lab
   user email), and set a deterministic `scoring_events.idempotency_key` that
   includes `workspace_id` + source so a child retry never double-awards.
2. On child startup, perform a one-shot `event_workspaces` check-in (DB upsert)
   recording workspace id/host/app url/version/last-seen. No outbox, no ingest
   API.
3. In `deploy.sh --role master`, provision the shared INSERT-only event-writer
   Postgres role: `INSERT` on `scoring_events`, `task_attempts`,
   `validation_results`, `hints_taken` + `SELECT` on the read tables/views and
   nothing else. Generate a random event-scoped password and surface the full
   credential (and an example child deploy command) in the success banner.
4. Add `scripts/federation_spike.py` to verify, from a child's perspective,
   network reachability, credential auth, and that the grant is correctly
   restricted (SELECT/INSERT ok; UPDATE/DELETE/DDL fail).

## Constraints

- Children never run migrations and never provision Lakebase.
- The writer credential must not be able to mutate or delete scores.
- Keep the standalone/master OAuth path unchanged.

## Suggested files

```text
app/services/federation.py
app/repositories/federation.py
app/main.py            # startup check-in hook
deploy.sh              # provision_event_writer_role
scripts/federation_spike.py
```

## Acceptance criteria

- A child write appears once; a duplicate submission is deduped by
  idempotency key.
- The event-writer role can only INSERT/SELECT.
- A freshly deployed child appears in `event_workspaces`.

## Verification

- `python scripts/federation_spike.py` against a test master Lakebase.
- Re-submit the same attempt from a child; confirm a single scoring event.
