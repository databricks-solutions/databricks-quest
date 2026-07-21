# 20 — State of the Nation: End-to-End Review & Pilot Readiness

**Date:** 2026-06-03
**Scope:** Full e2e review of PR01–PR12 plus the multi-workspace federation work
(PR13–PR16 / ADR_006), measured against each PR's stated goals and acceptance
criteria.
**Method:** Three independent code audits cross-checked against the prompts in
`prompts/`, plus direct verification of the highest-impact findings (test run,
validator-variable path, doc/code drift).

> **Bottom line:** The GameDay MVP is **functionally complete and well-tested at
> the unit/logic layer** — the frontend builds, and Adoption Mode is intact. It is
> **conditionally pilot-ready**: one core-gameplay correctness gap (team-scoped
> validator variables) and a small set of security/operational hardening items
> should be closed before a customer-facing pilot. None are architectural; all are
> scoped, low-risk fixes.

> **✅ UPDATE — Pilot Readiness sweeping PR (`feature/gameday-pilot-readiness`):**
> **All P0–P3 findings below are now closed**, the §5 test gaps are filled, and
> every §6 enhancement landed. **Both the single-workspace standalone and the
> federated master/child shapes are pilot-ready.** See the closed checklist in
> §7 and `docs/STATUS.md` for the current state. The findings (§4) and original
> checklist are retained verbatim below as the audit record that drove the work.

---

## 1. Verification snapshot

| Check | Result |
|-------|--------|
| Backend tests (`pytest tests/`) | ✅ **218 passed**, 7 warnings |
| Backend compiles (`compileall app notebooks`) | ✅ clean |
| Frontend build (`npm run build`) | ✅ clean (tsc + vite) |
| Adoption Mode endpoints intact | ✅ `/api/profile`, `/api/missions`, `/api/leaderboard`, `/api/admin/stats` unchanged |
| Event Mode gating (off by default) | ✅ GameDay routes 404 when `QUEST_EVENT_MODE` off; migrations skipped |
| Federation deploy flags exist as documented | ✅ `--event-mode`, `--role`, `--master-lakebase-*`, `--admins` present in `deploy.sh` |

The test suite is strong on **pure logic**: SQL safety, scoring idempotency,
event lifecycle state machine, namespace guards, report builder, federation
idempotency, writer-credential GRANT parsing. The thin spot is **HTTP/integration
and frontend** coverage (see §5).

---

## 2. Per-PR scorecard

| PR | Title | Verdict | Acceptance criteria |
|----|-------|---------|---------------------|
| PR01 | Domain model + migrations + DB module | ✅ Strong | 6/6 met (re-run untested) |
| PR02 | Configurable quest packs | ✅ Strong | 4/5 met, 1 partial (import/negative tests) |
| PR03 | Validation engine core | ✅ Strong | 5/5 met at logic layer; integration tests thin |
| PR04 | Event & team management | 🟡 Good w/ gaps | 3/5 met, 2 partial (per-event host role; audit assurance) |
| PR05 | Player gameplay experience | 🟡 Mostly met | Validator variables; no attempt polling; hint errors swallowed |
| PR06 | Admin host console | 🟡 Mostly met (standalone) | Master role doesn't mount the full host console |
| PR07 | Live scoring & leaderboard | ✅ Met | "Live" = manual refresh; tie-break untested |
| PR08 | Resource bootstrap & reset | 🟡 Mostly met | Validator/namespace disconnect; reset dry-run not in UI |
| PR09 | Sample GameDay packs | 🟡 Mostly met | `databricks_sdk` validators are lint-only, not executable |
| PR10 | Security, observability, audit | 🟡 Mostly met | Host allowlist open when unset; admin mutations unaudited |
| PR11 | Field reporting & export | ✅ Met | Participants is a count, not a roster |
| PR12 | Hardening, release, docs | 🟡 Mostly met | Stale `STATUS.md` sections; one doc path typo |
| PR13–16 | Federation (ADR_006) | ✅ Met | Writer can INSERT `quest_admins` (intentional, threat-model it) |

---

## 3. What works end-to-end today

The **standalone single-workspace GameDay loop is real and demonstrable**:

1. Host imports a quest pack (lint → immutable version) — `app/services/quest_pack_loader.py`, `app/repositories/quest_packs.py`.
2. Host creates an event + teams, runs the lifecycle (draft → ready → active → paused/frozen → completed → archived) — `app/repositories/events.py:31-39`, `app/main.py:1288-1293`.
3. Host bootstraps per-team catalogs/schemas with a namespace-guarded dry-run + execute, idempotently — `app/services/namespace.py`, `app/services/resource_service.py`.
4. Players join a team, open quests, submit attempts; SQL + manual validators run through a safety-checked engine; base points are awarded once (idempotent) — `app/services/validation_engine.py`, `app/services/scoring_service.py`.
5. A live leaderboard with podium, activity feed, freeze/final badge, and one-time hint penalties — `app/main.py:1070-1114`, `frontend/src/components/EventLeaderboard.tsx`.
6. Host console: lifecycle, team standings, attempts inspector with private diagnostics, announcements, manual adjustment, pack import, resources — `frontend/src/components/HostConsole.tsx`.
7. Post-event report (summary, completion matrix, blockers, champions, follow-ups) exportable as JSON/CSV/Markdown — `app/services/report_service.py`, `frontend/src/components/ReportPanel.tsx`.
8. **Federation plumbing** is in place: `QUEST_ROLE` seam, restricted event-writer credential (GRANT-verified in CI and a runtime spike), deterministic cross-workspace idempotency, roster import + identity reconciliation, and a shared global leaderboard.

Adoption Mode is untouched and continues to work independently.

---

## 4. Findings & gaps (by severity)

### 🔴 P0 — Close before a customer pilot

**P0-1. Team-scoped validator variables are not derived from the namespace.**
`_build_validator_variables` only populates `${team_catalog}` / `${team_schema}`
from the `teams` table columns (`app/main.py:1916-1920`). But teams created via
the host API/UI leave those columns **NULL** (`create_team` accepts them but the
endpoint doesn't set them — `app/repositories/events.py:403-436`), while resource
bootstrap derives the real target from `namespace.team_target()`
(`app/services/resource_service.py:59-80`). Net effect: a pack whose SQL
validators reference `${team_catalog}.${team_schema}` will **fail to template**
(the safety layer rejects unfilled slots) even after a successful bootstrap —
breaking the core "validate work in the team's schema" loop.
*Fix (small):* in `_build_validator_variables`, fall back to
`namespace.team_target(event, team_row)` when the columns are blank, and/or persist
the computed target back to the `teams` row during bootstrap. Add a test:
team with no explicit catalog → validator still resolves to the bootstrapped FQN.

**P0-2. Host surface is open when the allowlist is unset.**
With Event Mode on and `QUEST_HOST_ALLOWLIST` empty, **any authenticated user can
call `/api/host/*`** (`app/main.py:342-346`). For a customer pilot this must be a
hard requirement. *Fix:* fail closed (no host access) when the allowlist is empty
in non-dev deploys, or require `--admins`/host allowlist at deploy for any
`--event-mode`/`--role master` deploy; document loudly.

### 🟠 P1 — Strongly recommended before/at pilot

**P1-1. Master role does not mount the full host console.**
`App.tsx` routes `role === 'master'` to the federation console (roster/workspace
tools), not `HostConsole` — so a master host loses lifecycle, attempts,
announcements, adjustments, resources, and pack import in the UI
(`frontend/src/App.tsx`, `frontend/src/components/Federation.tsx`). Backend
endpoints exist; only the UI wiring is missing. *Fix:* render `HostConsole`
(or merge its panels) for the master role.

**P1-2. Dual/!inconsistent host authorization models.**
UI host tab is gated by `event_hosts` (`is_host` in the lobby), but host APIs are
gated by `QUEST_HOST_ALLOWLIST` — a user can see the tab and get 403, or be
allowlisted with no tab. Per-event host roles (`event_hosts`) are written on event
create but never enforced on mutations. *Fix:* pick one model (recommend
per-event `event_hosts` + a global admin override) and align UI + API.

**P1-3. `databricks_sdk` validators are lint-valid but not executable.**
The engine registry only has `sql_assertion` and `manual`
(`app/services/validation_engine.py:44-47`); unknown types return `SKIPPED`
("not available yet"). Sample packs pair them with `manual` validators so tasks
are still completable, but this should be explicit to facilitators. *Fix:* either
implement an SDK/workspace-API validator, or document clearly that SDK checks are
host-reviewed for the pilot (and ensure such tasks always carry a `manual`
validator).

**P1-4. Admin add/remove is not audited.**
`POST /api/admin/admins` and `DELETE /api/admin/admins/{email}`
(`app/main.py:656-721`) change the privilege boundary but write no audit row,
contradicting `docs/12` ("every mutation audited"). *Fix:* add `record_audit` to
both, with actor + target email.

**P1-5. `import_participants` can break the single-team invariant.**
Join/assign use `set_participant_team` (replace), but bulk import does an additive
`INSERT INTO team_members` (`app/repositories/events.py:596-605`), so a re-import
to a different team can leave a user on two teams. *Fix:* route bulk import through
`set_participant_team` (or delete prior memberships first).

### 🟡 P2 — Polish / robustness (during or shortly after pilot)

- **Validation result write failures are swallowed** (`app/main.py:2064-2065`) — an attempt can show `passed` with missing validator rows. Surface/log distinctly.
- **Audit is best-effort and post-write** (`app/services/audit.py:61-63`) — a failed audit doesn't roll back the mutation. Acceptable for MVP; document it.
- **Hint-reveal errors are swallowed in the UI** (`EventPlay.tsx` empty catch) — show a toast/inline error.
- **No async attempt polling** — backend exposes `GET .../attempts/{id}` but the UI never polls; `queued/running` states are decorative. Fine while validation is synchronous; revisit if SDK/long-running validators land.
- **Reset dry-run exists in code but has no UI button** (`HostConsole.tsx`); bootstrap dry-run is exposed but reset isn't.
- **Leaderboard is manual-refresh** ("live" via a Refresh button, not push). Acceptable for MVP; consider light polling for the pilot.
- **Dead/stale code:** `LeaderboardRepository.record_scoring_event` raises `NotImplementedError` (`app/repositories/leaderboard.py:161-164`); `repositories/__init__.py` docstring claims mutations are deferred; `events.assign_team` is unused.

### 🔵 P3 — Documentation drift (quick, low-risk)

- **`docs/STATUS.md` has stale lower sections** — lines ~63/71 still say "Tier 3 blocked on PR04" and "No event/team APIs yet," contradicting the PR table that marks PR04+ landed. (The headline "218 tests" is **correct**; an older "80 tests" tier note is stale.)
- **`docs/19_MANUAL_E2E_TEST.md:132`** load-test section uses `POST /api/events/{id}/tasks/{task_id}/submit` — the real endpoint is `.../attempts` (`app/main.py:1974`, `docs/08_API_CONTRACT.md`).
- **`docs/12_SECURITY_GOVERNANCE_COST.md`** lists `SHOW`/`DESCRIBE` as allowed SQL, but `ensure_safe_select` permits only `SELECT`/`WITH` (`app/validators/safety.py:108-110`). Align doc to code (or widen the allowlist deliberately).

---

## 5. Test-coverage assessment

**Strong (keep):** SQL safety, scoring idempotency, event lifecycle rules,
namespace guards + resource plans, report builder/renderers, federation
idempotency, writer-credential GRANT scope, host-console endpoint shaping.

**Gaps to fill for pilot confidence:**
1. **HTTP integration test of the core loop** — create event → import pack → start → join → submit (pass + fail) → score → leaderboard order, with stubbed repos. This is the single highest-value test to add.
2. **Two-team ranking + tie-break** ordering test (the view logic is untested).
3. **Quest-pack import** negatives: invalid YAML, duplicate hash → `duplicate`, immutable version conflict.
4. **Migration runner** apply-once / no-op-on-rerun.
5. **Validator variable resolution after bootstrap** (guards P0-1 from regressing).
6. **Federation HTTP** tests for `/api/federation/*` and a multi-workspace e2e (even mocked).
7. **Frontend**: at minimum a smoke test that the player and host flows render and call the right endpoints.

---

## 6. Enhancements (beyond gap-closing)

These are not defects — they increase pilot polish and field value:

- **Live leaderboard refresh** (short-interval polling or SSE) for the "GameDay buzz."
- **Participant roster in the report** (PR11 currently emits a participant *count*; a per-participant section helps account follow-up).
- **Team self-service** — let players create/rename teams in the lobby (today teams are host/API-created).
- **Validator dry-run for hosts** — a "test this task's SQL against a team schema" button to de-risk pack authoring before going live.
- **Executable `databricks_sdk`/`workspace_api` validator** — unlocks fully-automated checks for the richer sample-pack tasks.
- **In-app deploy/role banner** — surface `role`, `event_slug`, and host/admin status prominently (data already in `/api/health`).
- **Rate limiting / connection pooling** for large multi-workspace events (ADR_006 flags hundreds of child connections as an operational risk).

---

## 7. Pilot readiness checklist — ✅ CLOSED

All items below were closed by the `feature/gameday-pilot-readiness` sweeping PR.

**Before a customer-facing pilot (P0 + key P1):**

- [x] **P0-1** Fix team validator-variable resolution (namespace fallback) + test. — `_build_validator_variables` now falls back to `namespace.team_target`; bootstrap persists the computed FQN; covered by `tests/test_core_loop.py`.
- [x] **P0-2** Fail closed on empty host allowlist; require `--admins`/`--host-allowlist` for event-mode deploys. — `require_host` denies by default in Event Mode (only `QUEST_HOST_OPEN=1` opens it); `deploy.sh` fails without host authority.
- [x] **P1-1** Mount the full host console for the master role. — `App.tsx` mounts `HostConsole` for `master`, folding RosterImport/WorkspacesHealth/UnmappedIdentities in as sections.
- [x] **P1-3** SDK validators executable + every SDK task pairs a `manual` fallback. — `databricks_sdk`/`workspace_api` execute via `sdk_checks`; `/api/health` reports the live set.
- [x] **P1-4** Audit admin add/remove. — `record_audit("admin.add"/"admin.remove", …)` plus host add/remove auditing.
- [x] Run `docs/19_MANUAL_E2E_TEST.md` Track A (adoption) + Track B (GameDay) end-to-end on a fresh deploy.
- [x] Add the **core-loop HTTP integration test** (§5.1). — `tests/test_core_loop.py` drives submit→score→leaderboard over `TestClient`.

**Recommended at pilot:**

- [x] **P1-2** Unify host authorization; **P1-5** fix bulk-import single-team. — single fail-closed gate; `import_participants` routes through the delete-then-insert single-team pattern.
- [x] P3 doc fixes (STATUS, docs/19 path, docs/12 SQL rules, docs/08 endpoints).
- [x] Live leaderboard + attempt polling (pause when tab hidden).
- [x] If multi-workspace: federation HTTP tests (`tests/test_federation_http.py`) + child→master connection pooling; `scripts/federation_spike.py` available for live writer-grant confirmation.

**Decision for the pilot shape:** **Both shapes are now pilot-ready.** The
single-workspace standalone GameDay remains the lowest-risk first outing, and the
federated master/child shape is ready as a parallel/second pilot now that the
master host console, connection pooling, and federation HTTP tests have landed.

---

## 8. Known limitations to communicate to pilot stakeholders

- `databricks_sdk` validators are **host-reviewed**, not auto-executed (this release).
- Resource bootstrap/reset and `sql_assertion` validators **require a SQL warehouse**.
- Leaderboard updates on **refresh**, not push.
- Federation requires a **shared Lakebase reachable from child workspaces**, and the event-writer credential can INSERT into `quest_admins` (intentional, but threat-model for customer deployments).
- Audit logging is **best-effort** (does not roll back the underlying mutation on audit failure).

---

## 9. Suggested next-PR sequence

1. **PR-fix-A (P0):** validator-variable namespace fallback + host allowlist fail-closed + core-loop integration test.
2. **PR-fix-B (P1):** master host console wiring + unified host authorization + admin-mutation audit + bulk-import single-team fix.
3. **PR-fix-C (P3 + polish):** doc reconciliation (STATUS/19/12), reset dry-run button, hint-error surfacing, light leaderboard polling.
4. **PR-enh (post-pilot):** executable SDK validator, participant roster in report, team self-service, federation HTTP/e2e tests + connection pooling.

> All P0/P1 items are scoped, localized changes — no schema or architectural
> rework is required to reach a pilot-ready state.
