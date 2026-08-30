# Deploy Quest on Windows (and macOS / Linux) with `deploy.py`

`deploy.py` is a cross-platform Python installer that runs the whole deploy using
the Databricks SDK -- no bash, no `psql.exe`, no Terraform. The same command works
on Windows, macOS, and Linux.

## Prerequisites

- **Python 3.9+** on your PATH (`python --version`).
- **Databricks CLI** (for `databricks auth login`):
  - `winget install Databricks.DatabricksCLI`, or download the Windows zip from
    https://github.com/databricks/cli/releases/latest and put `databricks.exe`
    on your PATH.
- **Python packages:**
  ```
  pip install -r requirements.txt
  ```
  (That is `databricks-sdk`, `PyYAML`, and `psycopg2-binary` — pinned in
  `requirements.txt` at the repo root. `psycopg2-binary` is a wheel, so there is
  no PostgreSQL install needed. If you only ever use `--data-backend warehouse`
  you can skip `psycopg2-binary`, but the one-line install covers both backends.)

## Authenticate

```
databricks auth login --host https://YOUR_WORKSPACE.cloud.databricks.com
```

A browser opens for SSO. (Azure workspace URLs look like
`https://adb-XXXX.NN.azuredatabricks.net` — copy yours exactly.) `deploy.py`
also accepts `--profile NAME` or env `DATABRICKS_HOST` / `DATABRICKS_TOKEN`.

## Get the repo

```
git clone https://github.com/databricks-solutions/databricks-quest.git
cd databricks-quest
```

(No Git? Download the ZIP from the repo's green "Code" button and extract it.
The frontend ships pre-built, so there is nothing to build.)

## Choosing a catalog

Quest stores its scored tables in a Unity Catalog catalog. You do not need to
know which one up front. Leave `--catalog` off and the deploy asks:

```
  Quest stores its scored tables in a Unity Catalog catalog.
  Use an existing catalog, or create a new one:

    1) analytics_dev
    2) sandbox
    n) create a new catalog

  Choose [1]:
```

Picking an existing catalog is the safe default, and it has to be one you can
create schemas in. Choosing `n` lets you name a new one, but creating a catalog
needs `CREATE CATALOG` on the metastore, which is usually admin-only; if that is
refused the deploy stops before changing anything and lists the catalogs you can
use instead.

Not sure? Open **Catalog** in the workspace sidebar and pick one your team
already uses, or ask an admin to run:

```sql
CREATE CATALOG IF NOT EXISTS quest_data;
GRANT USE CATALOG, CREATE SCHEMA ON CATALOG quest_data TO `you@example.com`;
```

`--catalog NAME` skips the question, and it is required with
`--non-interactive`. If a catalog you name does not exist, you are asked once
before it gets created, so a typo cannot quietly make a second catalog.

## Deploy

The simplest, most portable option — **warehouse backend** (no Lakebase):

```
python deploy.py --data-backend warehouse
```

Or name the catalog yourself and skip the question:

```
python deploy.py --catalog YOUR_CATALOG --data-backend warehouse
```

That runs the full flow: auth check, warehouse select/create, catalog + schema
+ `app_settings`, upload app + notebooks, create the app, deploy it, grant the
app's service principal access, create the 4-hourly scoring job, and start the
first run. It prints the app URL at the end and takes roughly 5 minutes.

With no `--warehouse` or `--warehouse-id`, it uses the workspace's first SQL
warehouse (creating a small serverless one if there are none) and tells you which
it picked. You are only prompted to choose when you are sitting at a terminal;
piping the output or running from a script takes the default instead of hanging.

Re-running the same command is safe. It reuses the app, the warehouse, the
catalog, and the Lakebase instance, and updates the existing scoring job rather
than creating a second one. The update is partial, so tags, notifications, or
timeouts you added to the job by hand survive.

Pick one deploy tool per app and stay with it. The scoring job is named
`[Quest] Scoring Pipeline (<app-name>)`, which at the default app name is the
same name the Databricks Asset Bundle (`databricks.yml`) gives its managed job. If `deploy.py` finds a job
that a Databricks Asset Bundle owns, it leaves it alone and says so rather than
rewriting it behind the bundle's back; pass a different `--app-name` if you
want the two to coexist.

### Data backend

- `--data-backend warehouse` — the app reads the scored Delta tables through a
  SQL warehouse. No Lakebase, no Postgres, nothing to sync. This is the
  recommended starting point.
- `--data-backend lakebase` (the default) — provisions a Lakebase Postgres
  instance for sub-second reads. The scoring job then copies Delta into Lakebase
  after every run. Provisioning the instance adds about 5 minutes to the first
  deploy.

Either way an admin can flip the live backend later under **Admin → Data
Backend** without redeploying, which is why the deploy always wires up a
warehouse and the catalog.

## Flags

| Flag | What it does | Default |
|------|--------------|---------|
| `--catalog NAME` | Unity Catalog for Quest data | ask (required with `--non-interactive`) |
| `--schema NAME` | Schema for Quest tables | `quest` |
| `--app-name NAME` | Databricks App name | `databricks-quest` |
| `--data-backend {lakebase,warehouse}` | Deploy-time default backend | `lakebase` |
| `--warehouse "NAME"` | Use this existing warehouse (matched by name) | auto |
| `--warehouse-id ID` | Use this warehouse ID (skips lookup) | auto |
| `--admins a@b.com,c@d.com` | Seed Admin-page admins | deploying user |
| `--profile NAME` | Databricks CLI profile | env/default |
| `--host URL` | Workspace URL, when no profile or env auth is set | env/default |
| `--lakebase-host HOST` | Use an existing Lakebase endpoint (skips provisioning) | provision |
| `--lakebase-db NAME` | Lakebase database name | `quest_db` |
| `--skip-scoring` | Create the schedule but don't run scoring now | run it |
| `--non-interactive` / `-y` | Never prompt (CI / unattended) | prompt |

`deploy.py` deploys Quest's Adoption Mode.

## What runs where

- The **Quest app** runs on **Databricks Apps** (source-code runtime), not on
  your machine. `deploy.py` is a deploy tool that exits when the deploy
  finishes.
- The **scoring job** runs in Databricks every 4 hours. It has four tasks:
  `roundtrip_attestations` → `run_scoring` → `sync_to_lakebase` and
  `warm_warehouse`. The last two no-op for the backend you aren't using.
- The app is empty until the first scoring run finishes, which usually takes
  10-20 minutes. Watch it under **Workflows → [Quest] Scoring Pipeline**.

## Troubleshooting

- **"Could not create catalog"** — you do not have `CREATE CATALOG` on the
  metastore, or the account uses Default Storage and rejects `CREATE CATALOG`
  without an explicit managed location. The error lists catalogs you can use;
  re-run with one of those, or create one in the UI. If the catalog already
  exists, `deploy.py` never tries to create it.
- **Auth errors** — confirm `databricks auth login` succeeded
  (`databricks current-user me`), or that `--profile` / `DATABRICKS_HOST` +
  `DATABRICKS_TOKEN` are set.
- **App shows zeros** — the first scoring run has not finished yet. Check the
  job run, then reload.
- **Old `databricks-sdk`** — the Lakebase API moved between SDK releases.
  `deploy.py` handles both layouts, but if you see a Lakebase API error,
  `pip install -U databricks-sdk` resolves it.
