# ADR 001 — Keep Adoption Mode and Add Event Mode

## Status

Proposed

## Context

The current Databricks Quest app already provides value as an always-on platform adoption leaderboard. It scores user actions from system tables and presents dashboards, missions, badges, and leaderboards.

The new requirement is to support GameDay-style events with configurable quests and validation.

## Decision

Do not replace the current app. Add **GameDay Event Mode** alongside **Adoption Mode**.

## Consequences

### Positive

- Existing functionality remains usable.
- Migration risk is lower.
- Adoption scoring can become one validator type.
- Field teams can use the same product for ongoing adoption and live events.

### Negative

- The codebase must support two modes.
- Navigation and data model complexity increases.
- Care is needed to avoid confusing global missions with event tasks.

## Implementation notes

- Keep existing `/api/profile`, `/api/missions`, `/api/leaderboard` endpoints.
- Add `/api/events/*` and `/api/host/*` endpoints.
- Rename frontend concepts carefully: `Mission` for adoption, `QuestTask` for GameDay.
- Eventually represent current hard-coded missions as a built-in adoption quest pack.

### Event Mode is opt-in (added during PR03)

Adoption Mode is the **default**, and Event Mode must be **explicitly enabled**:

- A single switch, `QUEST_EVENT_MODE` (deploy flag `--event-mode`), defaults to
  **off**. The `master` and `child` federation roles imply it (a federated
  deploy is inherently an event), so it is forced on for them.
- When **off** (legacy default): every GameDay surface behaves as if it does not
  exist — the Event Mode API endpoints (`/api/events/*`, `/api/host/*`,
  `/api/federation/*`) return `404`, the Event/Host nav is hidden, and
  `deploy.sh` skips the GameDay schema migrations. The deployment is byte-for-byte
  the legacy adoption app.
- When **on**: the Event Mode API + UI activate. `standalone + --event-mode` is a
  single-workspace GameDay; `master`/`child` add federation.
- The resolution lives in one place (`app/config.py: event_mode_enabled()`), and
  `/api/health` reports `event_mode` so the frontend gates off the server's word.
- The switch is orthogonal to `QUEST_ROLE`, which now only selects federation
  topology *within* Event Mode.
