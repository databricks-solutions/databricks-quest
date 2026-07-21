# Databricks Quest -- Deployment Guide

This guide walks you through deploying Databricks Quest on any Databricks workspace. There are three ways to deploy:

| Method | Best For | Time | What It Does |
|--------|----------|------|-------------|
| **[Scripted Deploy](#scripted-deploy)** | Most users | ~15 min | One command handles everything |
| **[Manual Deploy](#manual-deploy)** | Full control, restricted environments, learning | ~30 min | You run each step yourself |
| **[Quick Deploy](#quick-deploy)** | Fast testing without DAB | ~10 min | App only, no scheduled scoring |

All three methods produce the same result: a running Quest app with scored data.

---

## Before You Start

### What You Need

**A Databricks workspace** with these features enabled:

- **Unity Catalog** -- enabled by default on workspaces created after November 2023. If your workspace was created earlier, ask your admin to enable it (Admin Console > Unity Catalog).
- **System tables** -- your admin needs to enable these. Open the SQL editor and run this query to check:
  ```sql
  SELECT * FROM system.billing.usage LIMIT 1
  ```
  If it returns a row, you're good. If you get "table not found", ask your admin to [enable system tables](https://docs.databricks.com/en/administration-guide/system-tables/index.html).
- **A SQL Warehouse** -- any SQL Warehouse that can access system tables. Most workspaces have a "Starter Warehouse" or "Serverless Starter Warehouse" created by default.
- **A writable catalog** -- the scoring pipeline writes scored Delta tables, so the deploying identity needs `CREATE SCHEMA` on the target catalog (or `CREATE CATALOG` to make a new one). On governed workspaces this is often locked down; ask a metastore admin to run, once:
  ```sql
  CREATE CATALOG IF NOT EXISTS quest;
  GRANT USE CATALOG, CREATE SCHEMA, CREATE TABLE, MODIFY, SELECT ON CATALOG quest TO `<deploying-user-or-SP>`;
  ```
  The deploy runs a pre-flight check and stops with the exact `GRANT` if this is missing, instead of failing later with an empty app. Do **not** point `--catalog` at `main` unless you have been granted `CREATE SCHEMA` there.

**On your local machine:**

| Tool | Version | How to Install | Required? |
|------|---------|---------------|-----------|
| Databricks CLI | v0.285+ | `brew install databricks/tap/databricks` (macOS) or [install guide](https://docs.databricks.com/en/dev-tools/cli/install.html) | Yes |
| Node.js | v18+ | `brew install node` (macOS) or [nodejs.org](https://nodejs.org) | No (pre-built frontend included) |
| psql | Any | `brew install postgresql@16` (macOS) or `apt install postgresql-client` (Linux) | Yes (for Lakebase) |

To check your versions:
```bash
databricks --version    # Need v0.285+
node --version          # Optional, v18+ if present
psql --version          # Any version
```

### What Gets Deployed

| Component | Description |
|-----------|-------------|
| **Databricks App** | React + FastAPI web app hosted on your workspace. Users log in with their existing workspace credentials. |
| **Scoring Notebook** | Spark notebook that reads system tables and computes missions, points, badges, and leaderboards. |
| **Scheduled Job** | Runs the scoring notebook every 4 hours to keep data fresh. (Scripted/Manual deploy only) |
| **Delta Tables** | 6 tables in your chosen catalog: mission_completions, user_profile_snapshot, leaderboard, badges, notifications, user_points_fact |
| **Lakebase Database** | Managed PostgreSQL database with the same 6 tables, synced from Delta. Gives the app sub-second response times. |

---

## Scripted Deploy

The fastest way to get running. One script handles authentication, warehouse selection, frontend build, bundle deployment, Lakebase provisioning, scoring, and data sync.

### Step 1: Clone and run

```bash
git clone https://github.com/deepbasu123/databricks-quest.git
cd databricks-quest
./deploy.sh
```

The script prompts you for:
1. **Workspace URL** -- copy this from your browser when logged into Databricks (e.g. `https://adb-1234567890.3.azuredatabricks.net` for Azure, or `https://my-workspace.cloud.databricks.com` for AWS)
2. **SQL Warehouse** -- pick one from the list
3. **Catalog name** -- where Quest's tables will live (e.g. `quest_data`)

That's it. The script handles everything else. It takes about 15 minutes, most of which is the scoring pipeline processing system table data.

### Non-interactive mode

If you know your settings ahead of time, skip all prompts:

```bash
./deploy.sh \
  --profile my-profile \
  --warehouse-id a1b2c3d4e5f67890 \
  --catalog quest_data
```

### All flags

| Flag | Description | Default |
|------|-------------|---------|
| `--profile NAME` | Databricks CLI profile to use | Prompts for workspace URL |
| `--warehouse NAME` | Select warehouse by name | Interactive prompt |
| `--warehouse-id ID` | Use this warehouse ID directly | Interactive prompt |
| `--catalog NAME` | Unity Catalog name for Quest data | Interactive prompt |
| `--schema NAME` | Schema name for Quest tables | `quest` |
| `--app-name NAME` | Custom app name | `databricks-quest` |
| `--target TARGET` | Bundle target (`dev` or `prod`) | `dev` |
| `--data-backend lakebase\|warehouse` | `warehouse` provisions BOTH Lakebase and a Small serverless SQL warehouse (1h auto-stop) and defaults the app to the warehouse. Admins switch at runtime under Admin -> Data Backend. | `lakebase` |
| `--lakebase-host HOST` | Use existing Lakebase endpoint | Auto-provisioned |
| `--lakebase-db NAME` | Lakebase database name | `quest_db` |
| `--skip-build` | Skip frontend build (use existing) | Builds if Node.js available |
| `--skip-scoring` | Skip running the scoring pipeline | Runs every time |
| `--skip-auth-check` | Skip auth validation (use if already logged in) | Validates auth |
| `--quick` | Quick deploy mode (no DAB bundle) | Full deploy |
| `--full` | Full deploy mode (DAB bundle + scoring job) | Default |

### What the script does (step by step)

1. **Checks prerequisites** -- verifies CLI, Node.js, psql versions
2. **Authenticates** -- opens browser for OAuth if not already logged in
3. **Selects SQL Warehouse** -- lists your warehouses and lets you pick one
4. **Asks for catalog** -- where to store Quest's Delta tables
5. **Builds frontend** -- runs `npm install && npm run build` if Node.js is available, otherwise uses pre-built files
6. **Deploys to Databricks** -- uses Databricks Asset Bundles to push the app, notebook, and scheduled job
7. **Provisions Lakebase** -- creates a Lakebase project, database, tables, and grants the app's service principal access
8. **Runs scoring pipeline** -- executes the scoring notebook once to populate data from system tables
9. **Syncs to Lakebase** -- reads scored Delta tables and writes them to Lakebase for fast app reads
10. **Prints app URL** -- shows where to open the app in your browser

---

## Manual Deploy

Use this if the script doesn't work in your environment, if you want to understand each step, or if your workspace has restrictions that prevent automated deployment.

### Step 1: Install prerequisites

```bash
# Install Databricks CLI (macOS)
brew install databricks/tap/databricks

# Install PostgreSQL client (macOS)
brew install postgresql@16

# Verify
databricks --version   # v0.285+
psql --version
```

### Step 2: Authenticate

```bash
# Replace with your workspace URL
databricks auth login --host https://YOUR_WORKSPACE.cloud.databricks.com

# Verify it worked
databricks current-user me
```

This opens your browser for OAuth login. After authenticating, the CLI saves a profile named after your workspace host.

**Azure users:** Your workspace URL looks like `https://adb-1234567890.3.azuredatabricks.net`. Copy it exactly from your browser address bar.

**Tip:** If you want to name your profile, add `--profile my-profile` and use `--profile my-profile` on all subsequent commands.

### Step 3: Clone the repo

```bash
git clone https://github.com/deepbasu123/databricks-quest.git
cd databricks-quest
```

### Step 4: Find your SQL Warehouse ID

```bash
databricks warehouses list
```

Copy the ID of the warehouse you want to use. If you don't have one, create a Serverless SQL Warehouse in the Databricks UI (SQL Warehouses > Create).

### Step 5: Create the catalog and schema

Open the Databricks SQL editor in your workspace and run:

```sql
CREATE CATALOG IF NOT EXISTS quest_data;
USE CATALOG quest_data;
CREATE SCHEMA IF NOT EXISTS quest;
```

Replace `quest_data` with whatever catalog name you want.

### Step 6: Build the frontend (optional)

If you have Node.js 18+, build the React app:

```bash
cd frontend
npm install
npm run build
cp -r dist/* ../app/static/
cd ..
```

If you don't have Node.js, the repo includes pre-built frontend files. Check that `app/static/index.html` exists. If it doesn't, you need Node.js to build.

### Step 7: Upload the scoring notebook

```bash
# Upload the notebook to your workspace
databricks workspace import-dir notebooks /Workspace/Users/YOUR_EMAIL/databricks-quest/notebooks --overwrite
```

Replace `YOUR_EMAIL` with your Databricks email address.

### Step 8: Run the scoring pipeline

The scoring pipeline reads system tables and populates the Quest Delta tables. You need to run it once to create initial data.

Option A -- Run via the Databricks UI:

1. Open your workspace
2. Navigate to the notebook at `/Workspace/Users/YOUR_EMAIL/databricks-quest/notebooks/scoring_pipeline`
3. Attach to any compute (Serverless recommended)
4. Set the widget parameters:
   - `quest_catalog`: your catalog name (e.g. `quest_data`)
   - `quest_schema`: `quest`
   - `app_name`: `databricks-quest`
   - `warehouse_id`: your warehouse ID
5. Click "Run All"

Option B -- Run via CLI:

```bash
databricks jobs submit --json '{
  "run_name": "Quest Scoring (one-time)",
  "tasks": [{
    "task_key": "run_scoring",
    "notebook_task": {
      "notebook_path": "/Workspace/Users/YOUR_EMAIL/databricks-quest/notebooks/scoring_pipeline",
      "base_parameters": {
        "quest_catalog": "quest_data",
        "quest_schema": "quest",
        "app_name": "databricks-quest",
        "warehouse_id": "YOUR_WAREHOUSE_ID"
      },
      "source": "WORKSPACE"
    },
    "environment_key": "default"
  }],
  "environments": [{
    "environment_key": "default",
    "spec": {"client": "1"}
  }]
}'
```

This takes 2-10 minutes depending on workspace size.

### Step 9: Create a scheduled job (recommended)

To keep data fresh, create a job that runs the scoring pipeline every 4 hours:

```bash
databricks jobs create --json '{
  "name": "[Quest] Scoring Pipeline",
  "tasks": [{
    "task_key": "run_scoring",
    "notebook_task": {
      "notebook_path": "/Workspace/Users/YOUR_EMAIL/databricks-quest/notebooks/scoring_pipeline",
      "base_parameters": {
        "quest_catalog": "quest_data",
        "quest_schema": "quest",
        "app_name": "databricks-quest",
        "warehouse_id": "YOUR_WAREHOUSE_ID"
      },
      "source": "WORKSPACE"
    },
    "environment_key": "default"
  }],
  "environments": [{
    "environment_key": "default",
    "spec": {"client": "1"}
  }],
  "schedule": {
    "quartz_cron_expression": "0 0 */4 * * ?",
    "timezone_id": "UTC"
  }
}'
```

### Step 10: Provision Lakebase

Lakebase gives the app sub-second reads. You need the Databricks CLI v0.285+ for these commands.

```bash
# Create a Lakebase project
databricks postgres create-project databricks-quest \
  --json '{"spec": {"display_name": "Databricks Quest"}}' \
  --no-wait

# Wait ~1 minute for the endpoint to become ACTIVE, then get the host
databricks postgres list-endpoints projects/databricks-quest/branches/production
```

Copy the `host` value from the output (looks like `ep-xxxxx.database.us-east-1.cloud.databricks.com`).

```bash
# Generate a credential to connect
databricks postgres generate-database-credential \
  projects/databricks-quest/branches/production/endpoints/primary

# Use the token and host to create the database
PGPASSWORD="YOUR_TOKEN" psql \
  "host=YOUR_LAKEBASE_HOST port=5432 dbname=postgres user=YOUR_EMAIL sslmode=require" \
  -c "CREATE DATABASE quest_db;"
```

Then create the tables:

```bash
PGPASSWORD="YOUR_TOKEN" psql \
  "host=YOUR_LAKEBASE_HOST port=5432 dbname=quest_db user=YOUR_EMAIL sslmode=require" \
  -c "
CREATE TABLE IF NOT EXISTS mission_completions (
  user_id TEXT, mission_id TEXT, mission_name TEXT, points_awarded INT,
  completed_at TIMESTAMP, period_start DATE, period_end DATE, scored_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_points_fact (
  user_id TEXT, event_type TEXT, mission_id TEXT, points INT,
  reason TEXT, event_timestamp TIMESTAMP, scored_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_profile_snapshot (
  user_id TEXT, display_name TEXT, total_points INT, level TEXT,
  current_streak INT, max_streak INT, badge_count INT, missions_completed INT,
  first_activity_date DATE, last_activity_date DATE, distinct_products_used INT,
  updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS leaderboard (
  user_id TEXT, display_name TEXT, total_points INT, weekly_points INT,
  monthly_points INT, level TEXT, all_time_rank INT, weekly_rank INT,
  monthly_rank INT, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS badges (
  user_id TEXT, badge_id TEXT, badge_name TEXT, badge_icon TEXT, earned_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notifications (
  id SERIAL PRIMARY KEY, user_id TEXT, notification_type TEXT, title TEXT,
  message TEXT, mission_id TEXT, points INT, created_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_user ON mission_completions(user_id);
CREATE INDEX IF NOT EXISTS idx_lb_rank ON leaderboard(all_time_rank);
CREATE INDEX IF NOT EXISTS idx_ups_user ON user_profile_snapshot(user_id);
CREATE INDEX IF NOT EXISTS idx_badges_user ON badges(user_id);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id);
"
```

### Step 11: Grant the app's service principal Lakebase access

```bash
# Get the app's service principal client ID
databricks apps get databricks-quest

# Create a Lakebase role for the SP (replace SP_CLIENT_ID)
databricks postgres create-role projects/databricks-quest/branches/production \
  --role-id quest-sp \
  --json '{"spec": {"identity_type": "SERVICE_PRINCIPAL", "postgres_role": "SP_CLIENT_ID", "auth_method": "LAKEBASE_OAUTH_V1", "membership_roles": ["DATABRICKS_SUPERUSER"]}}'

# Grant SELECT on tables
PGPASSWORD="YOUR_TOKEN" psql \
  "host=YOUR_LAKEBASE_HOST port=5432 dbname=quest_db user=YOUR_EMAIL sslmode=require" \
  -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO \"SP_CLIENT_ID\";
      ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO \"SP_CLIENT_ID\";"
```

### Step 12: Update app.yaml with Lakebase config

Edit `app/app.yaml` and replace the placeholder values:

```yaml
command:
  - uvicorn
  - main:app
  - --host
  - 0.0.0.0
  - --port
  - "8000"

env:
  - name: LAKEBASE_HOST
    value: "ep-xxxxx.database.us-east-1.cloud.databricks.com"
  - name: LAKEBASE_DB
    value: "quest_db"
```

### Step 13: Deploy the app

```bash
# Upload the app source code
databricks workspace import-dir app /Workspace/Users/YOUR_EMAIL/apps/databricks-quest --overwrite

# Create the app (first time only)
databricks apps create databricks-quest \
  --json '{"description": "Databricks Quest - Gamification for platform adoption"}'

# Deploy
databricks apps deploy databricks-quest \
  --source-code-path /Workspace/Users/YOUR_EMAIL/apps/databricks-quest
```

### Step 14: Grant the service principal catalog access

```sql
-- Run in the SQL editor
GRANT USE_CATALOG ON CATALOG quest_data TO `SERVICE_PRINCIPAL_NAME`;
GRANT USE_SCHEMA ON SCHEMA quest_data.quest TO `SERVICE_PRINCIPAL_NAME`;
GRANT SELECT ON SCHEMA quest_data.quest TO `SERVICE_PRINCIPAL_NAME`;
```

Get the service principal name from `databricks apps get databricks-quest` (look for `service_principal_name`).

### Step 15: Sync data to Lakebase

The sync copies data from your Delta tables to Lakebase so the app can read it quickly. Run this after every scoring pipeline execution, or set up a post-scoring sync.

The simplest approach is to run the sync section from the deploy script:

```bash
./deploy.sh --skip-build --skip-scoring \
  --catalog quest_data \
  --warehouse-id YOUR_WAREHOUSE_ID \
  --lakebase-host YOUR_LAKEBASE_HOST
```

Or do it manually using `psql` and the SQL Statements API. The sync reads each table from Delta and inserts it into Lakebase. See the `deploy.sh` source (Step 7b) for the full sync implementation.

### Step 16: Open the app

```bash
databricks apps get databricks-quest
```

Open the URL in your browser. You'll authorize the app once, then see your Quest dashboard.

---

## Quick Deploy

Quick deploy uses the Databricks Apps API directly without Databricks Asset Bundles. It deploys only the app. You upload and run the scoring notebook separately.

```bash
./deploy.sh --quick
```

Or combine with other flags:

```bash
./deploy.sh --quick --catalog quest_data --warehouse-id YOUR_ID
```

The difference from full deploy: no `databricks.yml` bundle configuration, no scheduled job. You manage the scoring notebook and Lakebase sync yourself.

---

## After Deployment

### Data stays fresh automatically

If you used the scripted deploy (full mode) or created a scheduled job in the manual deploy, the scoring pipeline runs every 4 hours. It re-reads system tables, rescores all missions, updates profiles, and rebuilds the leaderboard. The Lakebase sync happens at the end of each deploy script run -- for ongoing syncs, either re-run `./deploy.sh --skip-build` or set up a separate sync process.

### What users see

- **Dashboard** -- current level, total points, streak, badges, and the next missions to complete
- **Missions** -- 30+ missions across Data Engineering, Analytics, AI/ML, Streaming, Consumption, and Engagement categories
- **Leaderboard** -- top 10 users ranked by points. Resets weekly (every Saturday). The top 3 each week win swag.
- **Admin** -- pipeline health, user stats, mission completion charts, level distribution

### Estimated cost

| Component | Usage | Est. Daily Cost |
|-----------|-------|-----------------|
| Databricks App | Always on, minimal compute | ~$1-2 |
| Lakebase | Scales to zero when idle | ~$0.50-1 |
| SQL Warehouse | Wakes for scoring (~30 min/day) | ~$0.50-1 |
| Scoring Job | 6 runs/day x 5 min each | ~$0.30-0.60 |
| Delta Storage | KB-MB of scored tables | ~$0.00 |
| **Total** | | **~$2-5/day** |

---

## System Tables Used

The scoring pipeline reads these tables that Databricks maintains automatically:

| Table | What It Tracks | Missions It Powers |
|-------|---------------|-------------------|
| `system.billing.usage` | All compute usage per user | First Steps, DBU milestones, consumption points, streaks, product breadth |
| `system.lakeflow.jobs` | Job creation and configuration | Job Creator, Scheduler, Multi-Task Orchestrator |
| `system.lakeflow.job_tasks` | Task definitions within jobs | Multi-Task Orchestrator |
| `system.lakeflow.job_run_timeline` | Job execution history | Consistent Operator |
| `system.lakeflow.pipelines` | Pipeline creation | Pipeline Builder, Auto Loader Pioneer |
| `system.lakeflow.pipeline_update_timeline` | Pipeline execution results | Pipeline Runner |
| `system.query.history` | SQL query execution | Data Explorer, Power Analyst, AI Function Builder |
| `system.access.audit` | Workspace actions | Genie Creator, Dashboard Designer, Alert Creator, Model Deployer, MLflow Experimenter |

---

## Troubleshooting

### Authentication fails after browser login

**Symptom:** The script opens your browser, you log in successfully, the profile is saved, but the script reports "Authentication failed."

**Cause:** After OAuth login, the CLI creates a named profile (e.g. `adb-1234567890`), but the script's retry check doesn't know which profile to use. This is most common on Azure workspaces.

**Fix:** Update to the latest version of `deploy.sh` (this issue is fixed). Or use the workaround:

```bash
# Authenticate manually first
databricks auth login --host https://YOUR_WORKSPACE_URL

# Then run the script with --skip-auth-check
./deploy.sh --skip-auth-check --profile YOUR_PROFILE_NAME
```

Find your profile name with `databricks auth profiles`.

### "Cannot resolve bundle auth configuration" error

**Symptom:** Running `databricks current-user me` inside the `databricks-quest` directory fails with "config host mismatch".

**Cause:** The `databricks.yml` file has a placeholder workspace host (`https://YOUR_WORKSPACE.cloud.databricks.com`). The CLI tries to use it and conflicts with your actual profile.

**Fix:** Either:
- Run the command from outside the repo directory
- Use the `--profile` flag explicitly: `databricks current-user me --profile YOUR_PROFILE`
- Edit `databricks.yml` and replace `YOUR_WORKSPACE` with your actual workspace URL

### "Setup Required" message in the app

**Cause:** The app can't read data from Lakebase. This happens when:

1. **Scoring pipeline hasn't run yet.** Re-run it from Workflows > Job Runs in your workspace.
2. **Lakebase data not synced.** Re-run the sync: `./deploy.sh --skip-build --skip-scoring`
3. **App environment variables not set.** The app needs `LAKEBASE_HOST` and `LAKEBASE_DB` in `app.yaml`. Check that they have real values (not placeholders). Re-deploy after fixing.

### "Catalog does not exist and could not be auto-created"

Some workspaces require an explicit storage location when creating catalogs. Create the catalog manually:

1. In your workspace, go to **Catalog** in the left sidebar
2. Click **+ Add** > **Add a catalog**
3. Name it whatever you want (e.g. `quest_data`)
4. Re-run: `./deploy.sh --skip-build --catalog quest_data`

### No users on the leaderboard

System tables can take a few hours to populate after workspace creation. The scoring pipeline only finds users who have actual compute usage recorded in `system.billing.usage`. If your workspace is brand new, wait a few hours and re-run the pipeline.

### Permission errors on system tables

Ask your workspace admin to:

1. Enable system tables (Admin Console > System Tables)
2. Grant you access:

```sql
GRANT USE_CATALOG ON CATALOG system TO `your_email@company.com`;
GRANT USE_SCHEMA ON SCHEMA system.billing TO `your_email@company.com`;
GRANT SELECT ON SCHEMA system.billing TO `your_email@company.com`;
-- Repeat for: system.lakeflow, system.query, system.access
```

### App service principal can't read data

If the scoring pipeline couldn't grant the service principal access automatically, run these SQL commands. Find the service principal name with `databricks apps get databricks-quest`:

```sql
GRANT USE_CATALOG ON CATALOG quest_data TO `SERVICE_PRINCIPAL_NAME`;
GRANT USE_SCHEMA ON SCHEMA quest_data.quest TO `SERVICE_PRINCIPAL_NAME`;
GRANT SELECT ON SCHEMA quest_data.quest TO `SERVICE_PRINCIPAL_NAME`;
```

### Frontend build fails

**npm proxy or registry errors:** If you see errors connecting to an internal npm registry, point npm to the public registry:

```bash
npm config set registry https://registry.npmjs.org/
```

**Other npm errors:**

```bash
rm -rf frontend/node_modules
cd frontend && npm install && npm run build
```

### CLI version too old for Lakebase

Lakebase commands (`databricks postgres`) require CLI v0.285+. Upgrade:

```bash
# macOS
brew upgrade databricks/tap/databricks

# Linux
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
```

### Deploy script fails mid-way

The script is safe to re-run. It won't duplicate resources or data. Fix the underlying issue and run `./deploy.sh` again. Use `--skip-build` to save time if the frontend is already built.

---

## Glossary

| Term | What it is |
|------|------------|
| **Databricks App** | A web application hosted on your workspace. Gets its own URL, runs under a service principal, users log in with workspace credentials. |
| **Databricks Asset Bundle (DAB)** | Infrastructure-as-code for Databricks. The `databricks.yml` file defines what to deploy (apps, jobs, notebooks). |
| **System tables** | Read-only tables Databricks maintains about your workspace: who ran what, when, how much compute was used. They live in the `system` catalog. |
| **Lakebase** | Databricks' managed PostgreSQL service. Provides sub-second query responses for the app instead of waiting for a SQL Warehouse to wake up. |
| **Service principal** | A machine identity for apps. When you deploy a Databricks App, it gets its own service principal automatically. The SP needs grants to read your Quest tables. |
| **Unity Catalog** | Databricks' data governance layer. Organizes data into catalogs > schemas > tables. |
| **SQL Warehouse** | Compute for running SQL queries. Used by the scoring pipeline to read system tables. Can be serverless (starts in seconds) or classic. |
| **Delta Lake** | The storage format for Quest's scored data. Supports MERGE for idempotent updates. |
| **Scoring Pipeline** | A Spark notebook that reads system tables and computes which missions each user has completed, their points, badges, streaks, and leaderboard rank. |
