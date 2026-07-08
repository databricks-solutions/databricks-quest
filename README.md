# Databricks Quest

### 🌐 [**Overview & screenshots → databricks-solutions.github.io/databricks-quest**](https://databricks-solutions.github.io/databricks-quest/)

A gamification app that turns Databricks platform adoption into a game. Users earn points for building pipelines, running jobs, creating dashboards, querying data, and more. Weekly swag prizes keep things competitive.

Built entirely on Databricks: system tables for usage tracking, Delta Lake for scoring, Lakebase for fast reads, and Databricks Apps for hosting. Users log in with their existing workspace credentials.

---

## Two modes

Databricks Quest runs in two complementary modes from a **single codebase**, selected at deploy time:

| Mode | What it is | When to use | Enable |
|------|-----------|-------------|--------|
| **Adoption Mode** (default) | The passive, system-table-driven platform-adoption game described below — 30+ missions, weekly leaderboard, swag. Always on. | Ongoing internal adoption, always-on workspace engagement. | On by default. No flag needed. |
| **Event Mode (GameDay)** | Configurable, facilitator-run GameDay events: quest packs, teams, deterministic validators, live scoring/leaderboard, host console, per-team resource bootstrap, and post-event reporting. | Hands-on events, SE/SA enablement, customer workshops, competitive team challenges, hunter-account motions. | Opt-in: `./deploy.sh --event-mode` (or `QUEST_EVENT_MODE=on`). Implied by the `master`/`child` federation roles. |

Event Mode is **purely additive** — when it's off, the GameDay APIs return 404, the Event UI is hidden, and the GameDay migrations are skipped, so Adoption Mode behaves exactly as it always has. Event Mode can also span **multiple workspaces** (a master workspace aggregating child lab workspaces) via a shared Lakebase.

**Event Mode docs:**

- **[README_GAMEDAY.md](README_GAMEDAY.md)** — GameDay deployment & operations guide (what works today, per feature).
- **[docs/STATUS.md](docs/STATUS.md)** — authoritative per-PR status tracker.
- **[samples/packs/README.md](samples/packs/README.md)** — run & customize the shipped sample quest packs.
- **[samples/QUEST_PACK_SCHEMA.md](samples/QUEST_PACK_SCHEMA.md)** — quest pack authoring reference.
- **[samples/SAMPLE_EVENT_RUNBOOK.md](samples/SAMPLE_EVENT_RUNBOOK.md)** — facilitator event runbook.
- **[docs/17_TROUBLESHOOTING.md](docs/17_TROUBLESHOOTING.md)** — troubleshooting both modes.
- **[docs/19_MANUAL_E2E_TEST.md](docs/19_MANUAL_E2E_TEST.md)** — manual end-to-end test script + load-test guidance.

The rest of this README describes **Adoption Mode**.

---

## How scoring works (end to end)

Quest never asks users to self-report anything. Every point is derived from what
they actually did on the platform, read from **Unity Catalog system tables**. The
flow is:

```
Databricks system tables ──▶ Scoring pipeline (Spark notebook, every 4h)
  (read-only, account-wide)      detect missions · compute points/levels/badges
                                        │
                                        ▼
                          Delta tables in <catalog>.quest.*
                          (mission_completions, user_points_fact,
                           user_profile_snapshot, leaderboard,
                           badges, notifications)
                                        │
                     ┌──────────────────┴───────────────────┐
                     ▼                                        ▼
        Lakebase (Postgres, synced)              SQL warehouse (reads Delta)
                     └──────────────────┬───────────────────┘
                                        ▼
                        FastAPI backend ──▶ React app (Databricks App)
                                        (user logs in with workspace SSO)
```

### 1. Detection — what counts as "doing something"

The scoring notebook (`notebooks/scoring_pipeline.py`) runs one SQL query per
mission against system tables:

| Signal source | System table | Example missions |
|---|---|---|
| Compute usage | `system.billing.usage` | First Steps, weekly usage bonus, Daily Driver |
| Jobs & pipelines | `system.lakeflow.jobs`, `.job_run_timeline`, `.pipelines`, `.pipeline_update_timeline` | Job Creator, Pipeline Builder, Scheduler, Multi-Task Orchestrator |
| Queries | `system.query.history` | Data Explorer, Power Analyst, Auto Loader Pioneer, AI Function Builder, Liquid Clustering |
| Product actions (audit) | `system.access.audit` | Genie, dashboards, notebooks, apps, Lakebase, model serving, Vector Search, MLflow, UC grants |

Each mission's query returns the set of users who qualify, and the results are
`MERGE`d into `mission_completions`.

### 2. Human activity only — the core scoring rule

Quest is a game about **people adopting the platform**, not about machines running
workloads. System tables attribute a scheduled job or an always-on endpoint to
whoever it *runs as* — so without care, one person's nightly cron or a serving
endpoint could top the leaderboard while they're on vacation. The pipeline
prevents that:

- **Automated compute is excluded.** Billing rows carrying a `job_id` or
  `dlt_pipeline_id` are scheduled/automated and don't earn ongoing activity
  points (`INTERACTIVE_USAGE` filter).
- **Run-based missions count human-triggered runs only** — job runs with
  `trigger_type = ONETIME` and pipeline updates with `trigger_type = USER_ACTION`,
  never `CRON`/`PERIODIC`/`CONTINUOUS`.
- **The weekly usage bonus comes from an allow-list of interactive products**
  (`ALL_PURPOSE`, `INTERACTIVE`, `SQL`, `AI_FUNCTIONS`, `GENIE`) — never always-on
  machine products like `MODEL_SERVING` or `VECTOR_SEARCH`.
- **A weekly per-user cap** (`WEEKLY_CONSUMPTION_POINT_CAP`, default 500) means
  hands-on activity is what ranks people, not raw volume.
- **Service principals are swept out** — only email-shaped (human) identities are
  scored.

The result: the leaderboard reflects real human adoption, and someone who's
inactive drops even if workloads they created keep running.

### 3. Reward setup once, not every run

Creating something is a one-time achievement; running it repeatedly is not. So:

- **Creation missions** (Job Creator, Pipeline Builder, Scheduler, Auto Loader
  Pioneer, etc.) fire **once**, on the first time the platform action is detected
  (`WHEN NOT MATCHED` — idempotent).
- **Repeatable missions** (weekly query counts, monthly consumption, streaks) are
  recomputed from scratch each run (`DELETE` + re-insert) so tightened rules take
  effect and stale qualifications don't linger.
- **Tiered missions don't double-pay**: a week with 200+ queries earns Power
  Analyst (200 pts) *or* Data Explorer (150 pts), never both.

### 4. Points → levels, badges, leaderboard

- **Mission points** are summed per user into `user_points_fact`, then rolled up
  into `user_profile_snapshot` and `leaderboard` (all-time / weekly / monthly).
- **Weekly usage bonus**: a small points bonus for interactive hands-on work that week, capped per person.
- **Levels** are total-point thresholds: Bronze (0) → Silver (300) → Gold (800)
  → Platinum (2,000) → Elite (5,000).
- **Badges** are awarded for combinations: Platform Explorer (4+ products used),
  Consistent Contributor (14-day streak), Pipeline Craftsman (5+ pipeline
  missions), AI Pioneer (3+ AI/ML missions), Full Stack (missions in 5+
  categories).
- **Streaks** count consecutive active days and reset to 0 once activity lapses.

### 5. Serving the app

After scoring, the Delta tables are synced to **Lakebase** (managed Postgres) for
sub-second reads. The **FastAPI backend** reads from Lakebase (or directly from
the Delta tables via a **SQL warehouse**, selectable per deployment), and the
**React frontend** renders each user's dashboard, missions, leaderboard, badges,
and notifications. Users authenticate with their existing workspace credentials
(SSO) — no separate accounts.

The scoring job is idempotent and re-runnable: it runs every 4 hours on a
schedule with `max_concurrent_runs = 1`, so overlapping runs queue instead of
colliding.

---

## Deploy

Full instructions: **[SETUP.md](SETUP.md)** -- covers three deployment methods:

| Method | Best For | Time |
|--------|----------|------|
| **Scripted** (`./deploy.sh`) | Most users | ~15 min |
| **Manual** (step-by-step) | Full control, restricted environments | ~30 min |
| **Quick** (`./deploy.sh --quick`) | Fast testing without DAB | ~10 min |

Quick start:

```bash
git clone https://github.com/databricks-solutions/databricks-quest.git
cd databricks-quest
./deploy.sh
```

The script handles everything: prerequisites check, authentication, warehouse selection, frontend build, Lakebase provisioning, app deployment, scoring pipeline, and data sync. Takes about 15 minutes end to end.

### Data backend (Lakebase or SQL warehouse)

The app reads its scored adoption data from one of two backends, and admins can switch between them live:

- **Lakebase** (default) -- low-latency Postgres read model.
- **SQL warehouse** -- reads the scored Delta tables directly through a serverless SQL warehouse, bypassing Lakebase.

```bash
./deploy.sh --data-backend warehouse   # provision BOTH, default to warehouse
```

With `--data-backend warehouse`, the deploy provisions Lakebase **and** a Small, serverless SQL warehouse (1-hour auto-stop) and grants the app service principal access to both. Either way an admin can flip the active backend at runtime under **Admin -> Data Backend**, no redeploy needed.

### Useful flags

| Flag | What it does |
|------|--------------|
| `--data-backend warehouse` | Provision both backends; default to reading through the SQL warehouse. |
| `--skip-build` | Use the committed prebuilt frontend (no npm / registry access needed). |
| `--skip-scoring` | Deploy without running the scoring job now (it still runs on schedule). |
| `--profile <name>` | Use a specific CLI auth profile (recommended in restricted setups). |

> **Prerequisite:** the deploying identity must be able to create the scored-tables schema (`CREATE SCHEMA` on the target catalog, or `CREATE CATALOG`). The deploy runs a pre-flight check and fails fast with the exact `GRANT` if it can't. The 4-hour scoring schedule runs even in dev deployments.

---

## What Users See

- **Dashboard** -- Current level, points, streak, badges, and next missions to complete
- **Missions** -- 38 missions across Getting Started, Data Engineering, Analytics, AI/ML, Lakebase, Streaming, Engagement, and Governance, plus a **Business Users** track for Genie/dashboard/SQL/app work (the Missions page has a tab per category and track)
- **Leaderboard** -- ranked by points (all-time, weekly, monthly), weekly window resets every Saturday, with recognition for the top performers.
- **Admin** -- Pipeline health, user stats, mission completion charts, level distribution, and the **Data Backend** toggle (Lakebase / warehouse)

## Missions

### Getting Started & Data Engineering
| Mission | Points | What To Do |
|---------|--------|------------|
| First Steps | 25 | Use any Databricks compute for the first time |
| Job Creator | 100 | Create your first Lakeflow Job |
| Pipeline Builder | 150 | Create your first Lakeflow Spark Declarative Pipeline |
| Pipeline Runner | 200 | Run a pipeline successfully |
| Scheduler | 150 | Set up a scheduled or CRON-triggered job |
| Auto Loader Pioneer | 250 | Use Auto Loader in a pipeline |
| Multi-Task Orchestrator | 200 | Create a workflow with 3+ tasks |
| Liquid Clustering Adopter | 200 | Enable Liquid Clustering on a table |

### Analytics & Business Users
| Mission | Points | What To Do |
|---------|--------|------------|
| Genie Creator | 200 | Create an AI/BI Genie space |
| Genie Explorer | 100 | Ask a question in a Genie space |
| Genie Curator | 150 | Add instructions or sample questions to a Genie space |
| Genie Power User | 100 | Ask 10+ Genie questions in a week (repeatable) |
| AI Assistant | 100 | Use the Databricks Assistant (Genie) to write or fix code |
| Dashboard Designer | 150 | Create a Databricks Dashboard |
| Dashboard Explorer | 75 | View a published AI/BI dashboard |
| Dashboard Publisher | 150 | Publish a dashboard for others |
| Dashboard Operator | 150 | Schedule a dashboard delivery or subscription |
| Data Explorer | 150 | Execute 50+ SQL queries in a single week (repeatable) |
| Power Analyst | 200 | Execute 200+ SQL queries in a single week (repeatable) |
| Query Author | 75 | Save a query in the SQL editor |
| Alert Creator | 150 | Create a SQL Alert with a schedule |
| App Builder | 250 | Create and deploy a Databricks App |
| Notebook Author | 75 | Create your first notebook |

### Lakebase
| Mission | Points | What To Do |
|---------|--------|------------|
| Lakebase Builder | 250 | Create a Lakebase database instance |
| Lakebase Sync Builder | 250 | Sync a Unity Catalog table into Lakebase |
| Lakebase Database Creator | 150 | Create a Lakebase database or registered catalog |
| Lakebase Connector | 100 | Connect to Lakebase from an app or client |

### AI / ML
| Mission | Points | What To Do |
|---------|--------|------------|
| Model Deployer | 300 | Deploy a model to a serving endpoint |
| AI Function Builder | 250 | Use ai_query() in a SQL statement |
| Vector Search Pioneer | 200 | Create a Vector Search index |
| MLflow Experimenter | 150 | Log 10+ MLflow experiment runs |

### Streaming
| Mission | Points | What To Do |
|---------|--------|------------|
| Stream Starter | 250 | Run a Structured Streaming job for 24+ hours |

### Product usage (interactive)
| Mission | Points | What To Do |
|---------|--------|------------|
| SQL Analyst | 100 | Actively use a SQL Warehouse in a month (repeatable) |
| ML Practitioner | 150 | Actively use Model Serving in a month (repeatable) |

Plus a small **weekly usage bonus** for interactive hands-on work, capped per person so it can never dominate the leaderboard (see [How scoring works](#how-scoring-works-end-to-end)). Only interactive human usage counts — scheduled jobs, pipelines, and always-on endpoints are excluded — so points reflect real people using the platform, not machines running workloads.

### Engagement
| Mission | Points | What To Do |
|---------|--------|------------|
| Consistent Operator | 300 | Run jobs/pipelines on 7 days within 30 days (repeatable) |
| Daily Driver | 400 | Active on 20+ days in a 30-day window (repeatable) |
| Cross-Product Champion | 500 | Use 6+ distinct Databricks products in a month (repeatable) |

## Levels

| Level | Points Required |
|-------|----------------|
| Bronze | 0 |
| Silver | 300 |
| Gold | 800 |
| Platinum | 2,000 |
| Elite | 5,000 |

## Architecture

```
System Tables (read-only)          Quest App (Databricks App)
  system.billing.usage                 React Frontend
  system.lakeflow.jobs           <---  FastAPI Backend
  system.lakeflow.pipelines            reads from Lakebase
  system.query.history                 (sub-second queries)
  system.access.audit
        |
        v
  Scoring Pipeline (every 4h)
  runs as serverless job
        |
        v
  Delta Tables              --->  Lakebase (PostgreSQL)
  <catalog>.quest.*                quest_db
  mission_completions              synced after every
  user_profile_snapshot            pipeline run
  leaderboard
  badges, notifications
```

## Tech Stack

- **Frontend**: React 18, TypeScript, Tailwind CSS, Lucide icons, Vite
- **Backend**: FastAPI, Python 3.10+
- **Data**: Spark SQL, Delta Lake, system tables
- **Database**: Lakebase (managed PostgreSQL) for sub-second reads
- **Deployment**: Databricks Asset Bundles, Databricks Apps
- **Auth**: Workspace OAuth (SSO)

## Project Structure

```
databricks-quest/
  deploy.sh               # One-shot deployment script
  databricks.yml           # Bundle config (app, job, variables)
  app/
    main.py                # FastAPI backend (API endpoints)
    app.yaml               # Databricks App config
    requirements.txt       # Python dependencies
    static/                # Built React app (generated by npm run build)
  frontend/
    src/
      App.tsx              # Main app with sidebar navigation
      components/
        Dashboard.tsx      # User dashboard with stats and badges
        Missions.tsx       # Mission grid with completion status
        Leaderboard.tsx    # Top 10 with podium and swag prizes
        AdminPanel.tsx     # Admin stats and pipeline health
      types.ts             # TypeScript interfaces
    package.json
    vite.config.ts
  notebooks/
    scoring_pipeline.py    # Spark notebook that scores all missions
  SETUP.md                 # Full deployment guide & troubleshooting
```

## How to get help

Databricks support doesn't cover this content. For questions or bugs, please [open a GitHub issue](https://github.com/databricks-solutions/databricks-quest/issues) and the team will help on a best-effort basis.

## License

See [LICENSE.md](LICENSE.md). This project is provided under the Databricks License; see [NOTICE.md](NOTICE.md) for attribution.
