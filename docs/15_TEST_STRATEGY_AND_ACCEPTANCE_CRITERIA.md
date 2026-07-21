# 15 — Test Strategy and Acceptance Criteria

## Test layers

### 1. Manifest tests

Validate quest pack manifests:

- required fields present
- unique IDs
- valid point values
- valid validator types
- valid hint penalties
- valid unlock rules
- no unknown references

### 2. Backend unit tests

Test:

- quest pack loader
- validation engine dispatch
- scoring idempotency
- event lifecycle transitions
- role checks
- SQL safety checks

### 3. Integration tests

Test against a local or test Lakebase/Postgres:

- migrations
- event creation
- participant join
- attempt submission
- validation result persistence
- leaderboard update
- manual adjustment

### 4. Frontend tests

Minimum:

- build succeeds
- key pages render with mock API responses
- validation state components render all statuses
- host controls gated by role

### 5. Databricks workspace smoke tests

In a real workspace:

- deploy app
- run migrations
- import sample pack
- create event
- bootstrap team resources
- submit passing SQL validation
- submit failing SQL validation
- view leaderboard
- freeze event
- export report

## MVP acceptance criteria

### Existing adoption mode

- Existing dashboard loads.
- Existing missions load.
- Existing leaderboard loads.
- Existing admin stats load.
- Scheduled scoring job remains deployable.

### Quest pack import

- Valid sample pack imports.
- Invalid pack returns actionable errors.
- Imported pack has immutable version ID.
- Quest/task/validator counts are visible to host.

### Event management

- Host can create event from pack version.
- Host can create teams.
- Host can import participants.
- Player can join event.
- Event lifecycle controls work.

### Validation

- Player can submit attempt.
- SQL assertion validator passes and fails correctly.
- Manual validator supports host approval.
- Validation result is persisted.
- Scoring event is written only once for same completion.

### Leaderboard

- Team score updates after validation pass.
- Hint penalty reduces score.
- Manual adjustment changes score.
- Ranking is deterministic.
- Freeze blocks new scoring except host adjustments.

### Host console

- Host can see teams.
- Host can see validation queue/results.
- Host can send announcement.
- Host can manually adjust score with reason.
- Host can export report.

### Security

- Player cannot call host APIs.
- Player cannot validate against another team namespace.
- SQL validator blocks destructive SQL by default.
- Audit log records mutations.

## Performance acceptance criteria

For MVP event size:

- 100 participants
- 25 teams
- 10 quests
- 30 tasks
- 500 validation attempts/hour

Targets:

- player dashboard p95 API response under 800ms
- leaderboard p95 under 500ms
- sync SQL validator p95 under 5s
- async validator queued response under 500ms
- event host console usable with 25 active teams

## Manual test script

```text
1. Deploy app to test workspace.
2. Open app as host.
3. Import AI/BI sample quest pack.
4. Create event.
5. Import 6 participants across 2 teams.
6. Start event.
7. Log in as player.
8. Open event lobby.
9. Submit failing validation.
10. Confirm failure message appears.
11. Create required table/object.
12. Submit passing validation.
13. Confirm points awarded.
14. Confirm leaderboard updates.
15. Take a hint.
16. Confirm penalty applied.
17. Host sends announcement.
18. Player sees announcement.
19. Host freezes event.
20. Player cannot submit new attempt.
21. Host exports report.
22. Host resets resources.
```

## Release readiness checklist

- All MVP acceptance criteria pass.
- Security review completed.
- Docs updated.
- Sample pack validated.
- Event runbook tested.
- Cleanup tested.
- Cost estimate reviewed.
- Known limitations documented.
