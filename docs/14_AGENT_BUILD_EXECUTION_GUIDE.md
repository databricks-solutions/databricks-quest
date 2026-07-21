# 14 — Agent Build Execution Guide

## Recommended agent workflow

Use this package to drive an AI coding agent in a controlled, PR-by-PR sequence.

Do not ask the agent to build the whole platform in one pass. Use the individual PR prompts.

## Setup

1. Clone the repo.
2. Create a new branch from `main`.
3. Add this package under `docs/gameday-levelup/` or keep it outside the repo as build guidance.
4. Run the master prompt once.
5. Run one PR prompt per branch.

## Suggested branch naming

```text
feature/gameday-pr01-domain-model
feature/gameday-pr02-quest-packs
feature/gameday-pr03-validation-engine
feature/gameday-pr04-events-teams
feature/gameday-pr05-player-ux
feature/gameday-pr06-host-console
feature/gameday-pr07-live-leaderboard
feature/gameday-pr08-resource-bootstrap
feature/gameday-pr09-sample-packs
feature/gameday-pr10-security-observability
feature/gameday-pr11-reporting
feature/gameday-pr12-hardening-docs
```

## Build commands to preserve

The current repo uses:

```bash
cd frontend
npm install
npm run build
```

Backend deployment is via:

```bash
./deploy.sh
```

Do not break these paths unless the PR explicitly changes deployment.

## Agent guardrails

Every PR prompt should tell the agent:

- preserve existing adoption mode
- do not delete existing endpoints
- do not remove current deployment functionality
- add migrations idempotently
- keep Lakebase connection logic centralized
- use typed frontend API clients
- prefer small cohesive files over giant files
- add tests or manual verification steps
- document new env vars and flags

## Review checklist for every PR

- Does existing dashboard still work?
- Does `frontend` build?
- Does FastAPI start locally or in Databricks App?
- Are migrations idempotent?
- Are endpoints authenticated/scoped?
- Are errors safe for users?
- Are audit events written for mutations?
- Are docs updated?

## Execution order

Run prompts in this order:

1. `prompts/00_MASTER_AGENT_PROMPT.md`
2. `prompts/PR01_DOMAIN_MODEL_AND_MIGRATIONS.md`
3. `prompts/PR02_CONFIGURABLE_QUEST_PACKS.md`
4. `prompts/PR03_VALIDATION_ENGINE_CORE.md`
5. `prompts/PR04_EVENT_AND_TEAM_MANAGEMENT.md`
6. `prompts/PR05_PLAYER_GAMEPLAY_EXPERIENCE.md`
7. `prompts/PR06_ADMIN_HOST_CONSOLE.md`
8. `prompts/PR07_LIVE_SCORING_AND_LEADERBOARD.md`
9. `prompts/PR08_RESOURCE_BOOTSTRAP_AND_RESET.md`
10. `prompts/PR09_SAMPLE_GAMEDAY_PACKS.md`
11. `prompts/PR10_SECURITY_OBSERVABILITY_AND_AUDIT.md`
12. `prompts/PR11_FIELD_REPORTING_AND_HUNTER_SIGNALING.md`
13. `prompts/PR12_HARDENING_RELEASE_AND_DOCS.md`

## Recommended agent style

Use “plan → implement → verify → summarize”:

1. **Plan:** inspect repo and list exact files to change.
2. **Implement:** make code changes.
3. **Verify:** run build/tests/lint where available.
4. **Summarize:** produce PR summary and manual test plan.

## Do not allow

- broad rewrite of backend in a new framework
- frontend migration to a heavy UI framework
- replacing Databricks App deployment with unrelated hosting
- hard-coding sample event content in the backend
- arbitrary user-submitted SQL execution without safety checks
- validators that require account admin privileges for MVP
- breaking current adoption leaderboard
