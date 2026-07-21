# PR11 Prompt — Field Reporting and Hunter Signaling

## Branch

`feature/gameday-pr11-reporting`

## Goal

Generate useful post-event artifacts for enablement and sales follow-up.

## Requirements

1. Add event report service.
2. Add report API:
   - `GET /api/host/events/{event_id}/export?format=json|csv|markdown`
3. Report must include:
   - event summary
   - participants/teams
   - leaderboard
   - quest completion matrix
   - validation failures
   - hint usage
   - blockers
   - champions/high performers
   - recommended follow-ups
4. Add host UI export button.
5. Add Markdown report template.

## Acceptance criteria

- Host can export event report.
- Report is useful for account follow-up.
- CSV export includes teams/tasks/scores.
- Markdown export is readable.

## Verification

Run sample event and export report.
