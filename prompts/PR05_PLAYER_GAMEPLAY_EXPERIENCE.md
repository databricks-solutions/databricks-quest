# PR05 Prompt — Player Gameplay Experience

## Branch

`feature/gameday-pr05-player-ux`

## Goal

Add the frontend experience for participants in a GameDay event.

## Requirements

1. Add API client modules for events, quests, attempts, leaderboard.
2. Add event lobby page.
3. Add team gameplay dashboard.
4. Add quest list and quest detail/runner page.
5. Add validation submit UI.
6. Add validation status components for queued/running/passed/failed/error.
7. Add hint drawer UI.
8. Keep existing adoption dashboard accessible.

## Suggested frontend files

```text
frontend/src/api/client.ts
frontend/src/api/events.ts
frontend/src/api/validation.ts
frontend/src/routes/EventLobby.tsx
frontend/src/routes/EventDashboard.tsx
frontend/src/routes/QuestRunner.tsx
frontend/src/components/event/*
frontend/src/components/validation/*
```

## Constraints

- Do not break current sidebar navigation.
- If adding React Router, keep simple fallback route for adoption dashboard.
- Use existing Tailwind style system / brand kit if present.

## Acceptance criteria

- Player can view active events.
- Player can enter event lobby.
- Player can view team and quests.
- Player can submit validation attempt.
- Player can see pass/fail result.
- Frontend builds.

## Verification

```bash
cd frontend
npm run build
```
