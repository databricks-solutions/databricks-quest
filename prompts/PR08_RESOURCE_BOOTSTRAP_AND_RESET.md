# PR08 Prompt — Resource Bootstrap and Reset

## Branch

`feature/gameday-pr08-resource-bootstrap`

## Goal

Make event setup and cleanup repeatable.

## Requirements

1. Add resource bootstrap service.
2. Create team schemas from event/team config.
3. Seed sample data from quest pack resources.
4. Add dry-run endpoint.
5. Add reset/cleanup endpoint.
6. Add host UI for resource health.
7. Add safe namespace guards.

## Acceptance criteria

- Host can bootstrap all team resources.
- Team variables resolve correctly in validators.
- Dry-run catches missing resources/permissions.
- Reset refuses to drop resources outside configured namespace.
- Cleanup is logged.

## Verification

Manual workspace test with a sample pack and two teams.
