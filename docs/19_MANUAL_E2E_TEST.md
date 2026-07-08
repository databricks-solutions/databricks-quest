# 19 — Manual End-to-End Test Script

A scripted walkthrough to validate a fresh deploy by hand. Two tracks: **Adoption
Mode** (always on) and **Event Mode (GameDay)**. Each step lists the action and
the expected result. Includes load-test guidance at the end.

Prerequisites: a Databricks workspace, the Databricks CLI authenticated, Node +
Python installed, and a running SQL warehouse for Event-Mode resource/SQL steps.

---

## Track A — Adoption Mode (default deploy)

1. **Deploy.** `./deploy.sh`
   - *Expect:* prerequisites check, warehouse selected, frontend built, app
     deployed, Lakebase provisioned, scoring run. Prints the app URL.
2. **Open the app.** Visit the app URL and sign in with workspace SSO.
   - *Expect:* Dashboard loads with your profile (level, points, streak).
3. **Health.** `GET /api/health`
   - *Expect:* `200`, `federation.role: "standalone"`, `lakebase` check ok.
4. **Missions.** Open the Missions page.
   - *Expect:* 30+ missions across categories with completion status.
5. **Leaderboard.** Open the Leaderboard page.
   - *Expect:* ranked users (may be sparse until the scoring job has run).
6. **Admin.** Open the Admin page (as an allowlisted admin).
   - *Expect:* pipeline health and stats render.

✅ Adoption Mode verified — GameDay routes should be **404** in this deploy.

---

## Track B — Event Mode (GameDay)

### B1. Deploy with Event Mode

1. `./deploy.sh --event-mode --admins "<you@corp.com>"`
   - *Expect:* same as Track A plus GameDay migrations applied.
2. `GET /api/health`
   - *Expect:* `federation.role` present, `validator_types` non-empty, all
     subsystem `checks` healthy (incl. `sql_warehouse` if a warehouse is set).

### B2. Import a quest pack

3. In the Host console → **Pack importer**, paste
   `samples/packs/ai_bi_gameday.yml` and **Lint**.
   - *Expect:* zero errors, zero warnings.
4. **Import.**
   - *Expect:* a new immutable pack version with the quest/task counts shown.

### B3. Create an event + teams

5. Create a draft event from the imported pack version (you become owner-host).
   - *Expect:* event created in `draft`.
6. Create two teams (e.g. Red, Blue).
   - *Expect:* both listed in the Host console teams table.

### B4. Bootstrap team resources

7. Host console → **Resources** → **Dry-run plan (bootstrap)**.
   - *Expect:* `CREATE SCHEMA …` statements per team, all `within_namespace: true`,
     no blockers. (Works without a warehouse.)
8. **Bootstrap** (needs `QUEST_SQL_WAREHOUSE_ID`).
   - *Expect:* schemas created (idempotent), seed SQL applied, health → `active`.

### B5. Play the event

9. Move the event to **active** (lifecycle controls).
   - *Expect:* `attempts_open` becomes true.
10. As a player, open the event, **join a team** in the Lobby.
    - *Expect:* you're placed on the team; Submit becomes enabled.
11. Open the first quest and **submit** the warm-up task (a `SELECT 1`-style
    check).
    - *Expect:* validation passes; base points awarded once.
12. Open the **Standings** tab.
    - *Expect:* the live leaderboard shows your team with the awarded points and a
      recent-activity entry.
13. On a harder task, **reveal a hint**.
    - *Expect:* the hint body appears and a one-time penalty is deducted; revealing
      again does **not** re-charge.

### B6. Host operations

14. Post an **announcement**.
    - *Expect:* it appears in the player announcement banner.
15. Apply a **manual score adjustment** (with a reason).
    - *Expect:* the team's score changes; the adjustment is in the ledger + audit.
16. Open the **attempts inspector** on a submitted attempt.
    - *Expect:* full diagnostics (host-only) visible.

### B7. Freeze, complete, report

17. **Freeze** then **complete** the event.
    - *Expect:* attempts close; leaderboard shows a frozen/final badge.
18. Host console → **Report** panel.
    - *Expect:* summary tiles, champions, completion table, blockers, follow-ups.
19. Export **JSON**, **CSV**, **Markdown**.
    - *Expect:* three downloads with sensible filenames; CSV opens cleanly in a
      spreadsheet (one 0/1 column per task); Markdown is readable.

✅ Event Mode verified end-to-end.

---

## Track C — Multi-workspace federation (optional)

1. Deploy a **master**: `./deploy.sh --role master --admins "<you@corp.com>"`.
   - *Expect:* shared Lakebase + `quest_event_writer` role provisioned.
2. Deploy a **child**:
   `./deploy.sh --role child --master-lakebase-host <host> --master-lakebase-token <writer-credential>`.
   - *Expect:* `GET /api/federation/status` → `role: child`, `db_connected: true`.
3. Master host imports a **roster CSV** mapping child lab users → teams.
   - *Expect:* identities mapped; previously-unmapped scores re-attributed.
4. Play in the child; check the **global leaderboard**.
   - *Expect:* scores from the child appear in the master-aggregated leaderboard;
     the child sees its own team's rank.

---

## Load-test guidance

GameDay events are bursty — many teams submit near quest deadlines. Validate
capacity before a large event.

**What to size for**
- Concurrent players ≈ teams × members. Submissions cluster at quest open/close.
- The hot write path is `submit_attempt` → validators → `scoring_events`.
- The hot read path is the player **leaderboard** and **standings** polling.

**How to test**
- Use a load tool (`k6`, `locust`, or `hey`) against a staging deploy. Drive:
  - `GET /api/events/{id}/leaderboard` (read-heavy; simulate ~1 poll/5s/player).
  - `POST /api/events/{id}/tasks/{task_id}/attempts` (write path; stagger across
    teams/tasks).
- Pre-seed an event with representative teams/tasks (import a sample pack, create
  N teams, bootstrap resources).
- Watch `/api/health` `db_latency_ms` and the `lakebase` check under load.

**Targets / pass criteria**
- Leaderboard reads stay well under ~1s at expected concurrency.
- Submissions return without 5xx; idempotency holds (no double-award on retry).
- Lakebase latency stays bounded; no connection exhaustion.

**Tuning levers**
- Right-size the SQL warehouse used by `sql_assertion`/bootstrap.
- Lakebase is for low-latency reads — keep heavy analytics in Delta, not in the
  app's hot path.
- Reduce client poll frequency for standings if read load dominates.
- For very large multi-workspace events, scale the shared Lakebase and confirm
  child write throughput on the restricted writer role.

> This is guidance, not an automated suite. Capture the numbers you observe in the
> release notes so the next event has a baseline.
