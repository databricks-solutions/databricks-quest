# Project Status — Databricks Quest

**Single source of truth for where the build is.** Update this file as each PR
lands. Other docs (the PR plan, the GameDay README, per-doc implementation
notes) should point here rather than restating overall status.

- **Last updated:** 2026-06-03
- **Active branch / PR:** `feature/gameday-pilot-readiness` (stacked on `feature/gameday-pr12-hardening-release-docs`)
- **Plan of record:** [`13_PR_ALIGNED_SPRINT_PLAN.md`](13_PR_ALIGNED_SPRINT_PLAN.md)
- **Pilot readiness:** standalone **and** federated master/child are pilot-ready. See [`20_STATE_OF_NATION.md`](20_STATE_OF_NATION.md) for the closed P0–P3 checklist.

Legend: ✅ landed · 🟡 in progress · ⏳ planned (not started) · ⛔ blocked

---

## Modes

| Mode | Status | Notes |
|---|---|---|
| **Adoption Mode** | ✅ live | System-table scoring, missions, leaderboard, badges, admin. Unchanged. **The default** — Event Mode must be explicitly enabled. |
| **Event Mode (GameDay)** | 🟡 in progress (opt-in) | Off by default; enable with `--event-mode` / `QUEST_EVENT_MODE=on` (implied by `master`/`child` roles). When off, GameDay APIs 404, Event UI is hidden, and GameDay migrations are skipped. Schema, quest packs, validation/scoring write path (PR03), event/team lifecycle + join + attempt gating (PR04), and federation plumbing landed. End-to-end host→play→score→leaderboard works via API; player gameplay UI (PR05), host console UI (PR06), the live player leaderboard + hint-penalty scoring (PR07), namespace-guarded resource bootstrap/reset (PR08), two shipped sample quest packs (PR09), security/observability hardening — request ids, structured logs, expanded health, permission-model docs (PR10) — the post-event report with JSON/CSV/Markdown export for account follow-up (PR11), and release hardening + docs — dual-mode README, troubleshooting/release/E2E guides (PR12) — have landed. A sweeping pilot-readiness pass then closed the P0–P3 gaps: fail-closed unified host auth, the validator-variable namespace fallback, the executable `databricks_sdk` validator, the full master host console + child→master connection pooling, live-UX polish (leaderboard polling, attempt polling, hint-error surfacing, team self-service, health banner), a participant roster in the report, the core-loop HTTP test suite, and a committed quest-authoring agent skill. **Both standalone and federated master/child are pilot-ready.** |

---

## PR status

| PR | Capability | Status | Landed in |
|---|---|---|---|
| PR01 | GameDay domain model + Lakebase migrations + DB module | ✅ landed | `e6274d2` |
| PR02 | Configurable quest packs (manifest, loader, linter, import/list APIs, built-in pack) | ✅ landed | `2a959ab` |
| PR03 | Validation engine core (validator abstraction, SQL + manual validators, attempt submission, scoring idempotency) | ✅ landed | `feature/gameday-pr03-validation-engine` |
| PR04 | Event & team management (create events/teams/participants, join flow, lifecycle, attempt gating, single-team-per-event invariant, DB-backed admin allowlist shared across master/child with in-app admin management) | ✅ landed | `feature/gameday-pr04-events-teams` |
| PR05 | Player gameplay experience (event lobby, join/team picker, quests list, quest runner with submit + validation status + hints, team dashboard; player read endpoints `/team`, `/quests`, `/quests/{id}`) | ✅ landed | `feature/gameday-pr05-player-ux` |
| PR06 | Admin host console (lifecycle controls, teams/progress table, validation attempts inspector with private diagnostics, announcement composer + player banner, manual score adjustment, quest pack import/lint UI; host endpoints `/host/events/{id}` overview, `/teams`, `/attempts`, `/announcements`, `/adjustments`) | ✅ landed | `feature/gameday-pr06-host-console` |
| PR07 | Live scoring & leaderboard (player `/leaderboard` with podium + ranked table + activity feed + own-team highlight; deterministic tie-break; freeze/final badge; hint-penalty scoring via `/hints/{id}/reveal` — once-per-team, body withheld until revealed; manual adjustments already in ledger from PR06) | ✅ landed | `feature/gameday-pr07-live-leaderboard` |
| PR08 | Resource bootstrap & reset (namespace guard `services/namespace.py` as the sole authority; per-team catalog/schema targets + pack seed SQL; dry-run plan endpoint; bootstrap/reset via warehouse executor; reset refuses out-of-namespace targets; `event_resources` health table + host UI panel; migration 005) | ✅ landed | `feature/gameday-pr08-resource-bootstrap` |
| PR09 | Sample GameDay packs — `ai_bi_gameday.yml` + `lakehouse_foundations.yml` in `samples/packs/` (3 quests / 6 tasks each; SQL + databricks_sdk + manual validators; seed_sql for PR08 bootstrap; lint clean with zero warnings; run + customize guide) | ✅ landed | `feature/gameday-pr09-sample-packs` |
| PR10 | Security, observability, audit — request-id correlation (`X-Request-ID`) + standard error envelope; structured validation/scoring logs (`services/observability.py`); expanded `/api/health` subsystem checks (lakebase latency, migrations, validators, scoring, warehouse); permission-model docs; SQL-safety & role/scope tests | ✅ landed | `feature/gameday-pr10-security-observability` |
| PR11 | Field reporting & hunter signaling — post-event report service + `report_service.py` (pure builder + JSON/CSV/Markdown renderers); host endpoints `/host/events/{id}/report` (structured) and `/export?format=json\|csv\|markdown` (downloadable, audited); report covers summary, leaderboard, team×task completion matrix, validation failures, hint usage, blockers, champions/fastest team, and heuristic account follow-ups; CSV guarded against formula injection; host Report UI panel | ✅ landed | `feature/gameday-pr11-reporting` |
| PR12 | Hardening, release, docs — dual-mode `README.md` (Adoption + Event Mode overview + doc index); troubleshooting guide (`docs/17`); release checklist (`docs/18`); manual E2E test script + load-test guidance (`docs/19`); shared empty/error state polish (Report panel); adoption-mode regression confirmed (218 tests + clean build) | ✅ landed | `feature/gameday-pr12-hardening-release-docs` |

### Multi-workspace federation (ADR_006)

Federation was specced as PR13–PR16 but the **plumbing landed early on the PR02
branch** so later work wouldn't have to migrate twice. Gameplay-dependent parts
remain gated on PR03/PR04.

| PR | Capability | Status | Landed in |
|---|---|---|---|
| PR13 | Federation foundation (`QUEST_ROLE` seam, writer-credential branch, migration 002, deploy role flags) | ✅ landed | `2a959ab` |
| PR14 | Child wiring + shared writer credential (write stamping, deterministic idempotency, startup check-in, event-writer role) | ✅ landed | `2a959ab` |
| PR15 | Roster + identity reconciliation (roster CSV import, unmapped-identities + workspace-health endpoints) | ✅ landed | `2a959ab` |
| PR16 | Federation UX (child event leaderboard + own-team rank, master host console panels) | ✅ landed | `2a959ab` |

---

## What's deployable / testable today

See [`README_GAMEDAY.md`](../README_GAMEDAY.md#testing) for commands. Tiers:

- **Tier 0 — local, no Databricks** ✅: `pytest tests/` (258), `compileall`, frontend build (`npm run build`), offline quest-pack lint. Suites cover SQL-safety, expectation, dispatch, scoring idempotency, the **core gameplay loop over HTTP** (`test_core_loop.py`), the executable `databricks_sdk` validator (`test_sdk_validator.py`), quest-pack import negatives, the migration runner, and the federation HTTP surface — all pure / fake-DB. A Vitest frontend smoke suite (`frontend/src/test/`) runs in CI (`npm run test`).
- **Tier 1 — infra on deploy** ✅: standalone/master/child boot; `/api/health` reports role/event-mode, subsystem checks, registered validator types, and executable SDK check names; migration idempotency; quest-pack lint/import; connectivity + INSERT-only credential scope (`scripts/federation_spike.py`); child startup check-in in the master Workspaces panel.
- **Tier 2 — federation reads + gameplay write path** ✅: event/team rows created through the PR04 APIs (no seed SQL needed); attempt submission `POST /api/events/{id}/tasks/{task_id}/attempts` (SQL validator needs a warehouse — set `QUEST_SQL_WAREHOUSE_ID`; manual + `databricks_sdk` validators work with no warehouse), `validation_results`/`scoring_events` written, idempotent re-submit; roster import, `/api/federation/status` team resolution, `event_leaderboard` + `unmapped_identities` views.
- **Tier 3 — full end-to-end** ✅: child plays → validated → scored → master leaderboard → child sees rank. Event/team rows are created through the API (PR04); the master host console (PR-pilot D) surfaces lifecycle, teams, attempts, roster import, workspace health, and the report in one place.

---

## Known gaps / next steps

- **Scoring write path landed (PR03)** — submitting an attempt validates, persists `task_attempts` + `validation_results`, and awards base points once via `scoring_events` (idempotent per team/workspace). Manual validators return a pending state; the host manual-override UI is a later PR.
- **SQL validator needs a warehouse** — set `QUEST_SQL_WAREHOUSE_ID` (or per-validator `warehouse_id`); without one, `sql_assertion` returns a player-safe `error` and the host sees the diagnostic in `validation_results.private_message`. Safety/templating/expectation logic is warehouse-independent and unit-tested.
- **Host authority is fail-closed** — in Event Mode a `/api/host/*` caller must be in `QUEST_HOST_ALLOWLIST`, an admin, or an `event_hosts` row for the event; when no authority is configured anywhere, access is denied unless the `QUEST_HOST_OPEN=1` dev escape hatch is set. Manage per-event hosts via `/api/host/events/{id}/hosts`.
- **Federation writer credential** is a single shared secret distributed per event; rotate per event (see ADR_006). The child→master writer path uses a bounded connection pool (`LAKEBASE_POOL_MIN`/`MAX`). Per-child OAuth roles are a future hardening option.
- **Endpoints present today:** `/api/health` (role/event-mode/validator types/SDK checks), adoption reads, `/api/host/quest-packs/*` (lint/import), event lifecycle + `/api/host/events/{id}` overview/`teams`/`attempts`/`announcements`/`adjustments`/`hosts`/`report`/`export`/`bootstrap`/`reset`, `/api/events/{id}/join|team|teams|team/rename|quests|leaderboard`, `/api/events/{id}/tasks/{task_id}/attempts` (POST), `/api/events/{id}/attempts/{attempt_id}` (GET), `/api/events/{id}/tasks/{task_id}/hints/{id}/reveal`, `/api/federation/status|leaderboard`, `/api/host/events/{id}/roster|workspaces|identities/unmapped`.

---

## Reference

- PR plan: [`13_PR_ALIGNED_SPRINT_PLAN.md`](13_PR_ALIGNED_SPRINT_PLAN.md)
- GameDay deploy/ops: [`../README_GAMEDAY.md`](../README_GAMEDAY.md)
- Architecture: [`05_TARGET_ARCHITECTURE.md`](05_TARGET_ARCHITECTURE.md) · Data model: [`07_DATA_MODEL.md`](07_DATA_MODEL.md) · API: [`08_API_CONTRACT.md`](08_API_CONTRACT.md)
- ADRs: [`../adr/`](../adr/)
