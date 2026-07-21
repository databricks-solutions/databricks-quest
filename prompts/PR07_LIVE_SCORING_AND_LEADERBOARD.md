# PR07 Prompt — Live Scoring and Leaderboard

## Branch

`feature/gameday-pr07-live-leaderboard`

## Goal

Make team scoring and live leaderboards reliable.

## Requirements

1. Add team leaderboard API:
   - `GET /api/events/{event_id}/leaderboard`
2. Add recent scoring events API.
3. Add leaderboard view and podium components.
4. Add scoring freeze behavior.
5. Add deterministic ranking tie-breakers.
6. Add hint penalty scoring.
7. Ensure manual adjustments appear in scoring ledger.

## Acceptance criteria

- Passing validation updates team score.
- Hint penalty updates team score.
- Manual adjustment updates team score.
- Leaderboard sorts correctly.
- Freeze blocks new player scoring.
- Final leaderboard can be shown.

## Verification

Use 2 teams and submit attempts/hints/manual adjustments; verify ranking changes.
