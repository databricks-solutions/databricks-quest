# PR16 Prompt — Federation UX (child event view + master console)

## Branch

`feature/gameday-pr16-federation-ux`

## Goal

Give children event-wide visibility into the shared leaderboard and give hosts a
federation console — all from one build, with navigation that adapts to the
role.

## Requirements

1. Add `GET /api/federation/status` (any role) returning role, workspace id,
   event slug/id, whether this lab user is mapped, the resolved team, and a
   `db_connected` health flag.
2. Add `GET /api/federation/leaderboard` (child read) returning the event-wide
   `event_leaderboard` plus this workspace's own team (`you`) and `mapped`.
3. Child UI (`frontend/src/components/Federation.tsx`):
   - event-wide leaderboard with this workspace's own team + rank highlighted;
   - graceful "not yet mapped" state that still lets the player play;
   - DB-connection health indicator.
4. Master UI (same component, master branch): workspace-health panel
   (`event_workspaces`), roster import (textarea → roster import API), and
   unmapped-identities screen.
5. Role-aware navigation in `App.tsx`: show an **Event** item for children and a
   **Host Console** item for the master; standalone nav is unchanged.

## Constraints

- One build serves all roles; the UI branches on `/api/federation/status`.
- Reuse the existing quest UI primitives (`QuestCard`, `EmptyState`,
  `ErrorState`, `Skeleton`, `useApi`).
- Add loading, error, and empty states for every new view.
- Do not show federation nav in standalone mode.

## Suggested files

```text
frontend/src/types.ts
frontend/src/components/Federation.tsx
frontend/src/App.tsx
app/main.py   # /api/federation/status, /api/federation/leaderboard
```

## Acceptance criteria

- A child shows global standings and highlights its own team's rank from inside
  its workspace.
- An unmapped child shows the "not yet mapped" state and keeps playing.
- The master console shows workspace health, roster import, and reconciliation.
- Standalone UI is unchanged.

## Verification

- `cd frontend && npm install && npm run build`
- Manually exercise child and master roles via `QUEST_ROLE` against a seeded
  shared Lakebase.
