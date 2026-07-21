# PR04 Prompt — Event and Team Management

## Branch

`feature/gameday-pr04-events-teams`

## Goal

Add event, participant, and team lifecycle APIs.

## Requirements

1. Add event creation API.
2. Add event list/detail APIs.
3. Add participant join API.
4. Add host participant import API.
5. Add team creation and assignment APIs.
6. Add event status transitions:
   - draft
   - ready
   - active
   - paused
   - frozen
   - completed
   - archived
7. Add basic role enforcement for host APIs.
8. Write audit log for mutations.

## APIs

```text
GET /api/events
GET /api/events/{event_id}
POST /api/events/{event_id}/join
POST /api/host/events
POST /api/host/events/{event_id}/participants/import
POST /api/host/events/{event_id}/teams
POST /api/host/events/{event_id}/start
POST /api/host/events/{event_id}/pause
POST /api/host/events/{event_id}/freeze
POST /api/host/events/{event_id}/complete
```

## Acceptance criteria

- Host can create event from an imported pack version.
- Host can create teams.
- Player can join event.
- Event status blocks or allows attempt submission appropriately.
- Audit log records lifecycle changes.

## Verification

Use curl/manual API tests to create event, teams, participants, start/freeze event.
