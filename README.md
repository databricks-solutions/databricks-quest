# Databricks Quest

A gamification app that turns Databricks platform adoption into a game. Users earn points for building pipelines, running jobs, creating dashboards, querying data, and more. Weekly swag prizes keep things competitive.

Built entirely on Databricks: system tables for usage tracking, Delta Lake for scoring, Lakebase for fast reads, and Databricks Apps for hosting. Users log in with their existing workspace credentials.

**▶️ Watch the walkthrough**

<p align="center">
  <a href="https://youtu.be/orVYAYDhN3U">
    <img src="https://img.youtube.com/vi/orVYAYDhN3U/maxresdefault.jpg" alt="Databricks Quest video walkthrough" width="640">
  </a>
</p>

---

> **Deploy it into your own workspace** with `python deploy.py` -- see **[SETUP.md](SETUP.md)** for the full guide, or **[docs/DEPLOY_WITH_GENIE_CODE.md](docs/DEPLOY_WITH_GENIE_CODE.md)** to have **Genie Code** do it for you.

---

## How It Works

1. A **scoring pipeline** runs every 4 hours, reading Databricks system tables to detect what each user has done on the platform
2. It scores 58 missions across 6 pillars — Getting Started, Business Users, Data Engineering, Data Science, App Dev (Lakebase), and Governance — plus continuous consumption points based on DBU spend
3. Scored data is synced to **Lakebase** (managed PostgreSQL) for sub-second reads
4. A **React + FastAPI app** runs as a Databricks App, showing each user their dashboard, missions, leaderboard, and badges

No separate accounts needed. Users log in with their workspace credentials.

---

## Deploy

Quest deploys with **`deploy.py`**, a cross-platform Python installer that drives the Databricks SDK -- no bash, no `psql`, no Terraform, so the same command works on Windows, macOS, and Linux. Full instructions are in **[SETUP.md](SETUP.md)**; deploying through **Genie Code** is covered in **[docs/DEPLOY_WITH_GENIE_CODE.md](docs/DEPLOY_WITH_GENIE_CODE.md)**.

Quick start:

```bash
git clone https://github.com/databricks-solutions/databricks-quest.git
cd databricks-quest
pip install -r requirements.txt
databricks auth login --host https://YOUR_WORKSPACE.cloud.databricks.com
python deploy.py --catalog YOUR_CATALOG --data-backend warehouse
```

It asks which Unity Catalog to use (or takes `--catalog <name>`), creates the schema, uploads and deploys the Databricks App, grants the app's service principal access, creates the 4-hourly scoring job, and prints the app URL. Roughly 5 minutes, and re-running it is safe. See **[docs/WINDOWS_DEPLOY.md](docs/WINDOWS_DEPLOY.md)** for the full flag reference and prerequisites.

### Data backend (Lakebase or SQL warehouse)

The app reads its scored adoption data from one of two backends:

- **SQL warehouse** (`--data-backend warehouse`) -- reads the scored Delta tables directly through a serverless SQL warehouse. No Lakebase, no Postgres, nothing to sync; the simplest starting point.
- **Lakebase** (`--data-backend lakebase`, the default) -- provisions a Lakebase Postgres instance for low-latency reads and syncs Delta into it after every scoring run (about 5 minutes more on the first deploy).

```bash
python deploy.py --catalog YOUR_CATALOG --data-backend warehouse   # SQL warehouse only, no Lakebase
```

Either way the deploy also selects or creates a serverless SQL warehouse (the scoring job uses it) and grants the app's service principal access. Deploy with the default Lakebase backend to provision both a Lakebase read model and the warehouse, so an admin can flip the active backend at runtime under **Admin -> Data Backend** without redeploying.

### Useful flags

| Flag | What it does |
|------|--------------|
| `--catalog <name>` | Unity Catalog for Quest's scored tables (asked interactively if omitted). |
| `--data-backend warehouse` | Use the SQL warehouse backend only -- no Lakebase to provision or sync. |
| `--skip-scoring` | Deploy without running the scoring job now (it still runs on schedule). |
| `--profile <name>` | Use a specific CLI auth profile (recommended in restricted setups). |
| `--non-interactive` / `-y` | Never prompt (CI / unattended); requires `--catalog`. |

> **Prerequisite:** the deploying identity must be able to create the scored-tables schema (`CREATE SCHEMA` on the target catalog, or `CREATE CATALOG`). The deploy runs a pre-flight check and fails fast with the exact `GRANT` if it can't. The 4-hour scoring schedule runs even in dev deployments.

---

## What Users See

- **Dashboard** -- Current level, points, streak, badges, and next missions to complete
- **Missions** -- 58 missions across 6 pillars: **Getting Started**, **Business Users**, **Data Engineering**, **Data Science**, **App Dev (Lakebase)**, and **Governance** (the Missions page has a tab per pillar, plus an All Missions tab)
- **Leaderboard** -- Top 10 users ranked by points, resets every Saturday. Weekly swag prizes for the top 3.
- **Admin** -- Pipeline health, user stats, mission completion charts, level distribution, and the **Data Backend** toggle (Lakebase / warehouse)

## Missions

The Missions page groups every mission into 6 pillars (plus an **All Missions**
tab that shows everything). Each mission still carries its own accent color/category
for its tile; the pillar only controls which tab it shows up under.

### Getting Started
| Mission | Points | What To Do |
|---------|--------|------------|
| First Steps | 25 | Use any Databricks compute for the first time |
| Get Started: Data Engineering | 250 | Complete the free self-paced course (2 hrs) |
| Get Started: Machine Learning | 250 | Complete the free self-paced course (2 hrs) |
| Get Started: Generative AI | 250 | Complete the free self-paced course (2 hrs) |
| Get Started: SQL Analytics & BI | 250 | Complete the free self-paced course (2 hrs) |
| Get Started: Data Warehousing | 250 | Complete the free self-paced course (2 hrs) |
| Get Started: Platform Administration | 250 | Complete the free self-paced course (2 hrs) |
| Get Started: Data Governance | 250 | Complete the free self-paced course (2 hrs) |
| Get Started: Lakebase | 250 | Complete the free self-paced course (2 hrs) |
| Get Started: Lakehouse Architecture | 250 | Complete the free self-paced course (2 hrs) |
| Get Started: AI Agents | 250 | Complete the free course (2 hrs) |
| Databricks Learner | 500 | Complete 2+ Get Started courses |
| Daily Driver | 400 | Active on 20+ days in a 30-day window (repeatable) |
| Cross-Product Champion | 500 | Use 6+ distinct Databricks products in a month (repeatable) |

### Business Users
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
| SQL Analyst | 100 | 50+ SQL Warehouse DBUs in a month (repeatable) |

### Data Engineering
| Mission | Points | What To Do |
|---------|--------|------------|
| Job Creator | 100 | Create your first Lakeflow Job |
| Pipeline Builder | 150 | Create your first Lakeflow Spark Declarative Pipeline |
| Pipeline Runner | 200 | Run a pipeline successfully |
| Scheduler | 150 | Set up a scheduled or CRON-triggered job |
| Auto Loader Pioneer | 250 | Use Auto Loader in a pipeline |
| Multi-Task Orchestrator | 200 | Create a workflow with 3+ tasks |
| Liquid Clustering Adopter | 200 | Enable Liquid Clustering on a table |
| Stream Starter | 250 | Run a Structured Streaming job |

### Data Science
| Mission | Points | What To Do |
|---------|--------|------------|
| Model Deployer | 300 | Deploy a model to a serving endpoint |
| AI Function Builder | 250 | Use ai_query() in a SQL statement |
| Vector Search Pioneer | 200 | Create a Vector Search index |
| MLflow Experimenter | 150 | Log 10+ MLflow experiment runs |
| Model Registry Curator 🆕 | 200 | Register a model in the Unity Catalog Model Registry |
| Feature Store Builder 🆕 | 200 | Create a Feature Store table for ML feature engineering |
| ML Practitioner | 150 | Any Model Serving DBUs in a month (repeatable) |

### App Dev (Lakebase)
| Mission | Points | What To Do |
|---------|--------|------------|
| Lakebase Builder | 250 | Create a Lakebase database instance (Provisioned or Postgres/Autoscaling) |
| Lakebase Sync Builder | 250 | Sync a Unity Catalog table into Lakebase |
| Lakebase Database Creator | 150 | Create a Lakebase database or registered catalog |
| Lakebase Connector | 100 | Connect to Lakebase from an app or client |
| Lakebase Branch Master 🆕 | 200 | Create a branch in a Lakebase Postgres project |
| Lakebase Role Architect 🆕 | 150 | Create a custom PostgreSQL role in Lakebase |
| Lakebase Reverse ETL Pioneer 🆕 | 250 | Stream Lakebase changes back into Unity Catalog with Change Data Feed |

### Governance
| Mission | Points | What To Do |
|---------|--------|------------|
| Unity Catalog Publisher | 150 | Share a table across schemas |
| Catalog Architect 🆕 | 150 | Create a Unity Catalog catalog |
| External Location Pioneer 🆕 | 200 | Configure secure access to cloud storage with an external location |
| Lakehouse Federation Pioneer 🆕 | 250 | Connect an external database system with Lakehouse Federation |
| Data Sharer 🆕 | 200 | Share data with another organization using Delta Sharing |
| Consistent Operator | 300 | Run jobs/pipelines on 7 days within 30 days (repeatable) |

Plus **continuous consumption points**: 1 point per 10 DBUs consumed, scored weekly. This keeps the leaderboard dynamic and rewards sustained platform usage.

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
  deploy.py                # One-command deployment (cross-platform, SDK-driven)
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

## License

MIT
