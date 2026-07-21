# Design: Get Started training missions + learning-resource links

**Date:** 2026-07-17
**Status:** Superseded in part by the Revision below (self-serve tick-box)
**Repo:** databricks-solutions/databricks-quest

> **REVISION (2026-07-17, later same day) — self-serve tick-box + free-only courses.**
> After the admin-feed version was built and deployed, the direction changed:
>
> 1. **Tick-box instead of admin feed.** Users tick a course as complete and
>    confirm; points are awarded on confirmation, written to the backend DB, and
>    appear instantly on their profile + the leaderboard. This is a deliberate move
>    to **self-reported / honor-system** points (a user can tick without doing the
>    course) — the opposite of the original no-self-report choice. Accepted knowingly.
> 2. **Free courses only.** The point-earning missions become the **10 free
>    self-paced (2h) Get Started courses**; the paid $75 3h self-paced editions are
>    dropped, and links point only to the free edition. (Verified against the live
>    Academy catalog: the instructor-led editions are also free, but self-paced is
>    the right "free, self-serve, tick-and-go" fit. AI Agents has no free *self-paced*
>    edition, so it links its free *instructor-led* page.) Points unchanged: 250 per
>    course + 500 Databricks Learner bonus at 2+ courses.
>
> See the "Revision: self-serve tick-box" section at the end for the full design.
> The sections below describe the original admin-feed version; the feed table it
> introduces (`training_completions`) is REUSED by the revision as the durable
> record of ticks, so it is not thrown away.

## Summary

Two additive features on top of Adoption Mode:

1. **Get Started course missions (points).** Award points when a user completes
   Databricks Academy instructor-led (ILT) "Get Started" courses, filed under the
   existing **Getting Started** category. Each completed course earns points, and a
   one-time **Databricks Learner** bonus fires at 2+ completed courses ("count them
   as a learner").
2. **Learning-resource links (no points).** Embed the matching *self-paced*
   "Get Started" course link on each of the 10 new course-missions as a learning
   resource, in addition to the existing docs link.

Both are purely additive: no existing mission, point value, table, or endpoint
changes. The feature is dormant until a completions feed table has rows.

## Core constraint that shaped this design

Quest's founding rule (README + code): **points are never self-reported.** Every
one of the 38 existing missions is detected by a SQL `MERGE` against Unity Catalog
`system.*` tables in `notebooks/scoring_pipeline.py`. There is no manual /
attestation / "I did this" award path in Adoption Mode (the only manual award is
Event-Mode host score adjustment for teams).

Databricks Academy course completions **do not live in Unity Catalog system
tables** — Academy is a separate platform (Docebo LMS at
`customer-academy.databricks.com`). To stay faithful to the no-self-report
principle, completions enter Quest through a **Delta feed table** that the customer
or an admin lands from an Academy/LMS export. The scoring pipeline only ever
*reads* that table, exactly like it reads a system table. No user ever clicks
"I completed this."

## Course data (verified against the live Academy catalog)

All course names, IDs, durations, and slugs below were pulled from the Databricks
Academy Docebo API (`/learn/v1/courses?search=get started`) and independently
re-verified by a second model on 2026-07-16/17. The public course URLs return HTTP
200 without authentication.

### Instructor-led (ILT / `course_type=classroom`) — these earn points

The canonical English "Get Started" ILT series is 10 courses. Each is 2 hours
(`duration=7200s`) except Lakebase (3 hours / `10800s`). Each mission matches on
the English course ID **plus its language-variant IDs**, so a Japanese/Korean/etc.
edition of the same course counts once toward the same mission.

| Mission | English ILT id | All classroom ids (incl. language variants) |
|---------|----------------|---------------------------------------------|
| Get Started: Data Engineering | 1511 | 1511, 2438, 2439, 2496, 3908, 3933 |
| Get Started: Machine Learning | 1512 | 1512, 3518, 3519, 3521 |
| Get Started: Generative AI | 3514 | 3514, 3665, 3667, 3669, 3751 |
| Get Started: SQL Analytics & BI | 1513 | 1513, 3393, 3394, 3395, 3728, 3760 |
| Get Started: Data Warehousing | 3553 | 3553, 4182, 4196, 4210 |
| Get Started: Platform Administration | 1509 | 1509, 4471, 4499, 4535 |
| Get Started: Data Governance | 4653 | 4653, 4829, 4831 |
| Get Started: Lakebase (3h) | 5082 | 5082, 5400, 5448, 5454, 5673, 5674 |
| Get Started: Lakehouse Architecture | 3532 | 3532, 4276, 4292, 4298 |
| Get Started: AI Agents | 4459 | 4459 |

The "Get Started Days" combo courses and "Knowledge Check" items are intentionally
excluded from the mapping. If a feed row's `course_id` is not in any mission's ID
set, it is ignored (and counted in a log line).

### Self-paced (`course_type=elearning`) — learning-resource links only, no points

Public URL format (verified 200, no login):
`https://customer-academy.databricks.com/learn/courses/{id}/{slug}`

Where a course has both a 2-hour and a 3-hour self-paced edition, **both** are
linked. AI Agents has **no** self-paced edition, so that mission shows no
learning-resource row.

| Mission | Self-paced edition(s) linked (id · duration) | slug |
|---------|----------------------------------------------|------|
| Get Started: Data Engineering | 2469 · 2h, 2521 · 3h | get-started-with-databricks-for-data-engineering |
| Get Started: Machine Learning | 2460 · 2h, 2461 · 3h | get-started-with-databricks-for-machine-learning |
| Get Started: Generative AI | 2724 · 2h, 3480 · 3h | get-started-with-databricks-for-generative-ai |
| Get Started: SQL Analytics & BI | 3347 · 2h, 3350 · 3h | get-started-with-sql-analytics-and-bi-on-databricks |
| Get Started: Data Warehousing | 3603 · 2h, 3604 · 3h | get-started-with-databricks-for-data-warehousing |
| Get Started: Platform Administration | 2453 · 2h, 2454 · 3h | get-started-with-databricks-platform-administration |
| Get Started: Data Governance | 4677 · 2h, 4678 · 3h | get-started-with-data-governance-on-databricks |
| Get Started: Lakebase | 5081 · 2h, 5114 · 3h | get-started-with-lakebase |
| Get Started: Lakehouse Architecture | 3509 · 2h (only edition) | get-started-with-lakehouse-architecture-on-databricks |
| Get Started: AI Agents | *(none — no self-paced edition exists)* | — |

## Architecture

```
Academy/LMS completion export  ──▶  <catalog>.quest.training_completions  (Delta feed, admin-landed)
                                                    │
                              scoring_pipeline.py reads it (like any system table)
                                                    │
                     ┌──────────────────────────────┼───────────────────────────────┐
              per-course missions              "Databricks Learner" bonus       (self-paced rows
        (one_time, one per ILT course)      (one_time, fires at >=2 courses)      never score)
                     └──────────────────────────────┴───────────────────────────────┘
                                                    │
                                    Delta quest.mission_completions
                                                    │  (existing flow)
                                    Lakebase / warehouse ──▶ FastAPI ──▶ React
```

## Component 1 — Feed table (data contract)

New Delta table, created empty by the scoring notebook if missing (so deploys never
break when no feed exists):

```sql
CREATE TABLE IF NOT EXISTS <catalog>.quest.training_completions (
  user_id      STRING,     -- MUST match Quest identity (workspace email / run_as)
  course_id    STRING,     -- Docebo course id, e.g. '1511'
  course_name  STRING,     -- e.g. 'Get Started with Databricks for Data Engineering'
  course_type  STRING,     -- 'classroom' (ILT) | 'elearning' (self-paced)
  completed_at TIMESTAMP
) USING DELTA
```

- Only `course_type='classroom'` rows score. Self-paced rows are ignored by scoring.
- The customer/admin owns landing this table from an Academy export. It is out of
  scope for Quest to fetch from Academy (no first-party completions API is assumed).

**Identity-matching risk (must be documented loudly).** `user_id` must equal the
identity Quest keys on (`system.billing.usage.identity_metadata.run_as`, effectively
the workspace email). Academy is a separate login; emails usually match but are not
guaranteed. If they don't match, points silently don't award. Mitigation: the
scoring block logs `matched N feed users / dropped M unmatched` so the mismatch is
diagnosable, and SETUP.md documents the requirement.

## Component 2 — Scoring (`notebooks/scoring_pipeline.py`)

Two new detection blocks following the existing
`MERGE ... WHEN NOT MATCHED THEN INSERT` idempotent pattern, reading
`training_completions` instead of a `system.*` table:

1. **Per-course missions** (10 blocks, or one parameterized loop): for each course,
   `MERGE` a completion row when the user has any feed row with
   `course_type='classroom' AND course_id IN (<that course's id set>)`.
   `completed_at = MIN(feed.completed_at)` for that user+course. 250 points each.
2. **Databricks Learner bonus**: `MERGE` a `databricks_learner` completion when a
   user has `COUNT(DISTINCT <normalized course>) >= 2` across classroom feed rows.
   500 points, `one_time`. Normalization collapses language variants to one course
   (map every classroom id to its English base course before counting distinct).

Both are safe to re-run; already-awarded users are not double-credited (the existing
`WHEN NOT MATCHED` semantics on `(user_id, mission_id)`).

## Component 3 — Mission definitions (`app/main.py`)

Append 11 entries to `MISSION_DEFINITIONS`, all `"category": "Getting Started"`:

- 10 course-missions: `award_type: "one_time"`, `points: 250`, an `icon`, a
  `doc_url` (Academy course landing or relevant docs page), and the two new fields
  below.
- 1 `databricks_learner` bonus: `award_type: "one_time"`, `points: 500`.

New optional mission fields (all existing missions omit them and are unaffected):

- `learning_url?: string` — single self-paced URL, OR
- `learning_resources?: {label: string, url: string}[]` — used when a course has
  both 2h and 3h editions (chosen so a mission can carry multiple links cleanly).

Decision: use `learning_resources` (a list) as the canonical field, since 9 of 10
missions link 1–2 editions and AI Agents links 0. A single `learning_url` cannot
represent the 2-edition case. Missions with an empty/absent list render no
learning-resource row.

Scoring uses a parallel `course_ids` mapping. Whether `course_ids` is exposed in the
`/api/missions` response or stripped is an implementation detail (default: strip it
to keep the API lean); `learning_resources` **is** exposed.

`/api/missions` (`app/main.py:597`) already does `m.copy()` over each definition and
merges completion status, so new fields flow to the frontend with no endpoint change.

## Component 4 — Frontend (`frontend/src/`)

- `types.ts` `Mission` interface: add `learning_resources?: {label: string; url: string}[]`.
- `components/quest/MissionDrawer.tsx`: below the existing primary docs `<a>` (the
  orange "Learn how to complete this" button, ~line 98), render a **"LEARNING
  RESOURCE" labeled row** when `mission.learning_resources?.length`:

  ```
  ┌──────────────────────────────────────┐
  │ [ Learn how to complete this      → ] │  ← existing, unchanged (doc_url)
  │                                        │
  │ LEARNING RESOURCE                      │  ← eyebrow label
  │ [ 📚 Get Started: … (2 hr)        → ] │  ← outline link, one per edition
  │ [ 📚 Get Started: … (3 hr)        → ] │
  └──────────────────────────────────────┘
  ```

  Outline/secondary styling so the docs link stays primary. Each edition is its own
  link, labeled with its duration. `target="_blank" rel="noreferrer"` like the docs
  link. Purely conditional — missions without the field look exactly as today.

- **Scope:** only the 10 new Getting Started course-missions render this row.
  Existing platform missions are unchanged (no links added to Data Engineering /
  Analytics / etc. missions).

## Error handling & edge cases

- **No feed table** → notebook creates it empty; the 10 course-missions + Learner
  bonus stay unearned; deploy stays green.
- **Feed row, unknown `course_id`** → ignored; counted in a log line.
- **Identity mismatch** (Academy email ≠ workspace email) → row doesn't match a
  Quest user; scoring logs matched-vs-dropped counts. Not silent.
- **Self-paced completion in feed** → never scores (filtered to `classroom`).
- **AI Agents mission** → exists and scores; simply renders no learning-resource row.
- **Dead external link** → catalog URLs verified 200 today but are external; a stale
  link degrades to a normal click-through, never an app error.

## Testing

- **Offline unit tests** (`tests/`, existing pattern):
  - feed→mission id-set mapping (English id and a language-variant id both map to the
    same mission),
  - Learner threshold: 0/1 course → no bonus; 2/3 distinct → bonus; 2 rows that are
    language variants of the *same* course → NOT a bonus (distinct-course count = 1),
  - self-paced (`elearning`) rows never score.
- **Frontend**: MissionDrawer renders N learning-resource links for a mission with N
  editions, and none when the field is absent; AI Agents mission → docs link only.
- **Scoring SQL**: seed a synthetic `training_completions` covering users with
  0/1/2/3 courses, a language variant, a self-paced row, and an unknown id; assert
  exact `mission_completions` rows and points.
- **Docs**: README Missions table gains the Getting Started course rows; SETUP.md
  gains a "Training completions feed" section documenting the table contract and the
  identity-matching requirement.

## Rollout

Additive and reversible. No change to existing missions, points, tables, or
endpoints. Ships with the normal scoring run; dormant until the feed table has
`classroom` rows whose `user_id` matches Quest users.

## Out of scope (YAGNI)

- Fetching completions directly from an Academy API (none assumed to exist).
- Scoring self-paced completions (links only).
- Marking a learning-resource link "completed" from self-paced feed rows (possible
  later using the same feed, but not now).
- Adding learning links to existing non-course missions.
- Tiers beyond the single Learner threshold.

---

# Revision: self-serve tick-box (points self-reported, instant, durable)

## What changes vs the admin-feed version
- Users self-attest course completion via a **checkbox + confirm dialog** on each
  Get Started mission. No admin export needed.
- Points are **self-reported (honor system)** — accepted knowingly.
- Points are awarded **on confirmation**, written to the backend DB, and reflected
  **instantly** on the user's profile and the leaderboard.
- Missions are the **10 free self-paced (2h) Get Started courses**; paid editions
  dropped; links point to the free edition only.

## Course set (verified free against the live Academy catalog, 2026-07-17)
`price = 0, selling = false`. Free self-paced (elearning) 2h editions:

| Mission id | Course | Free self-paced id | Link |
|---|---|---|---|
| gs_data_engineering | Data Engineering | 2469 | courses/2469/... |
| gs_machine_learning | Machine Learning | 2460 | courses/2460/... |
| gs_generative_ai | Generative AI | 2724 | courses/2724/... |
| gs_sql_analytics_bi | SQL Analytics & BI | 3347 | courses/3347/... |
| gs_data_warehousing | Data Warehousing | 3603 | courses/3603/... |
| gs_platform_admin | Platform Administration | 2453 | courses/2453/... |
| gs_data_governance | Data Governance | 4677 | courses/4677/... |
| gs_lakebase | Lakebase | 5081 | courses/5081/... |
| gs_lakehouse_architecture | Lakehouse Architecture | 3509 | courses/3509/... |
| gs_ai_agents | AI Agents | 4459 (free ILT — no free self-paced exists) | courses/4459/... |

Paid editions dropped from `learning_resources` entirely: 2521, 2461, 3480, 3350,
3604, 2454, 4678, 5114 ($75 3h) and the instructor-led paid catalog.

## The durability problem (why a naive INSERT is wrong)
The scoring pipeline is the source of truth for the serving tables and REBUILDS
them every 4h:
- **Lakebase mode:** `notebooks/lakebase_sync.py` does `DELETE FROM {table}` then
  reinserts from Delta for `mission_completions`, `user_points_fact`,
  `user_profile_snapshot`, `leaderboard`, `badges`, `notifications`.
- **Warehouse mode:** the app reads Delta directly, and `db.execute()` **raises**
  (`"write not supported on warehouse data backend (read-only)"`).

So a tick written only into Lakebase `mission_completions` is DELETED on the next
scoring run, and a tick is impossible at all in warehouse mode.

## Design: Lakebase durable store + round-trip to Delta (closed loop)

The app can only durably write to **Lakebase** (the warehouse backend is read-only,
and it has no transactional single-row Delta write). But the scoring pipeline reads
and reconciles from **Delta**, and `lakebase_sync.py` REBUILDS the Lakebase serving
tables from Delta every cycle by full `DELETE`+reinsert. So a tick written only into
the Lakebase serving tables is wiped on the next sync. The closed loop:

1. **Durable store → new Lakebase table `training_attestations`** (NOT one of the
   serving tables the sync truncates). On confirm the app writes:
   `(user_id, course_mission_id, course_id, attested_at)` — idempotent on
   `(user_id, course_mission_id)`. This table is the permanent record of ticks and
   is never overwritten by the scoring rebuild.
2. **Instant serving write (Lakebase), same request.** Existence-guarded inserts
   into `mission_completions` + `user_points_fact` (the base DDL has NO unique
   constraint — scoring keeps them clean by full DELETE+reinsert, so we use
   `WHERE NOT EXISTS`, not `ON CONFLICT`), plus increment the user's
   `user_profile_snapshot` + `leaderboard` total (level re-derived; ranks stay
   approximate until the next scoring run recomputes them globally). Databricks
   Learner (+500) awarded when the user reaches ≥2 distinct completed courses.
3. **Round-trip Lakebase → Delta in `lakebase_sync.py`, BEFORE the push.** The
   existing sync task (runs every cycle after `run_scoring`, already holds both a
   Lakebase psycopg2 connection and Spark/Delta access) first reads
   `training_attestations` from Lakebase and `MERGE`s new ticks into the Delta
   `training_completions` table as `course_type='self_attested'` rows (idempotent
   on user_id+course_id). THEN it does its normal Delta→Lakebase overwrite.
4. **Reconciliation.** Because the ticks are now in Delta `training_completions`,
   the NEXT `run_scoring` Step 2b re-derives the same completions (idempotent MERGE
   on user_id+mission_id, `WHERE completed_at IS NOT NULL`, matching `course_type IN
   ('classroom','self_attested')` on the course id-sets which now include the free
   self-paced ids). The subsequent sync overwrites the Lakebase serving tables from
   Delta with identical rows — so the instant-write rows are replaced by identical
   scored rows: no loss, no double-count. Points stay visible throughout.

Net: durable store is `training_attestations` (Lakebase), ticks round-trip to the
Delta UC table, and both "instant" and "survives the rebuild" hold.

## Backend endpoint
`POST /api/training/attest {course_mission_id}`:
- validate `course_mission_id` is one of the 10 gs_* ids (400 otherwise)
- if `get_data_backend() == 'warehouse'`: return 409 with a clear message (writes
  unsupported; the tick-box requires the Lakebase backend). UI hides/disables the
  checkbox in that mode.
- else: append feed row + instant serving upsert (all idempotent) in one
  `transaction()`; return the updated mission + new total_points.
- Idempotent: re-attesting an already-completed course is a no-op (returns done).

## Frontend
- `MissionDrawer` for a training mission (`detection: 'training'`): replace the
  "awarded from Academy loaded by your admin" copy with a **checkbox + "I confirm I
  completed this course"**. On confirm → POST attest → optimistic flip to done,
  points animate. Show a small honor-system note ("Self-reported").
- Warehouse mode (read from a `/api/config` flag or the 409): render the checkbox
  disabled with "Switch to the Lakebase backend to self-report course completions."
- `learning_resources` reduced to the single free edition per course.
- Confirm-only (no un-tick) for now.

## Testing
- Offline: attest awards once; re-attest is a no-op; 2nd distinct course triggers
  Learner; warehouse mode refuses the write; feed row shape matches Step 2b's
  reader so reconciliation reproduces the same rows.
- Idempotency: simulate app-write THEN a scoring run over the same feed → identical
  mission_completions/user_points_fact (no duplicate, no loss).

## Out of scope (unchanged)
- Un-tick / reversal.
- Making the tick-box work in warehouse mode (it's read-only by design).
