# Deploy Quest on Windows (and macOS / Linux) with `deploy.py`

`deploy.sh` is a bash script and does not run on Windows. `deploy.py` is a
cross-platform Python installer that does the same job using the Databricks SDK
— no bash, no `psql.exe`, no Terraform. The same command works on Windows,
macOS, and Linux.

## Prerequisites

- **Python 3.9+** on your PATH (`python --version`).
- **Databricks CLI** (for `databricks auth login`):
  - `winget install Databricks.DatabricksCLI`, or download the Windows zip from
    https://github.com/databricks/cli/releases/latest and put `databricks.exe`
    on your PATH.
- **Python packages:**
  ```
  pip install databricks-sdk psycopg2-binary PyYAML
  ```
  (These are the same packages Quest's app uses. `psycopg2-binary` is a wheel —
  no PostgreSQL install needed. Lakebase mode needs a recent `databricks-sdk`;
  see the note under "Data backend" below.)

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

## Deploy

The simplest, most portable option — **warehouse backend** (no Lakebase):

```
python deploy.py --catalog quest_data --data-backend warehouse
```

That runs the full flow: auth check, warehouse select/create, catalog + schema
+ `app_settings`, upload app + notebooks, create the app, deploy it, grant the
app's service principal access, run the scoring pipeline, and create the
4-hourly scheduled job. It prints the app URL at the end.

### Data backend

- `--data-backend warehouse` — the app reads scored Delta tables through a SQL
  warehouse. No Lakebase, no Postgres. **Works with the SDK version Quest pins**
  and is the recommended starting point on Windows.
- `--data-backend lakebase` (the default) — provisions a Lakebase Postgres
  instance for sub-second reads. This path needs a **newer `databricks-sdk`
  than the version pinned in `app/requirements.txt`** (the Lakebase credential
  API is not in the pinned release). If you want Lakebase, first run
  `pip install -U databricks-sdk`. If the SDK is too old, `deploy.py` stops with
  a clear message rather than failing halfway.

## Flags

| Flag | What it does | Default |
|------|--------------|---------|
| `--catalog NAME` | Unity Catalog for Quest data | (required) |
| `--schema NAME` | Schema for Quest tables | `quest` |
| `--app-name NAME` | Databricks App name | `databricks-quest` |
| `--data-backend {lakebase,warehouse}` | Deploy-time default backend | `lakebase` |
| `--warehouse "NAME"` | Use this existing warehouse (matched by name) | auto |
| `--warehouse-id ID` | Use this warehouse ID (skips lookup) | auto |
| `--admins a@b.com,c@d.com` | Seed Admin-page admins | deploying user |
| `--profile NAME` | Databricks CLI profile | env/default |
| `--skip-scoring` | Deploy without running scoring now | run it |
| `--non-interactive` / `-y` | Never prompt (CI / unattended) | prompt |

## What runs where

- The **Quest app** runs on **Databricks Apps** (source-code runtime), not on
  your machine. `deploy.py` is a deploy tool that exits when the deploy
  finishes.
- The **scoring job** runs in Databricks on its 4-hourly schedule.

## Troubleshooting

- **"generate_database_credential is unavailable in this databricks-sdk
  version"** — you're deploying `--data-backend lakebase` with too old an SDK.
  Either `pip install -U databricks-sdk`, or use `--data-backend warehouse`.
- **Auth errors** — confirm `databricks auth login` succeeded
  (`databricks current-user me`), or that `--profile` / `DATABRICKS_HOST` +
  `DATABRICKS_TOKEN` are set.
- **No catalog** — the deploying identity needs permission to create the catalog,
  or pre-create it and pass its name with `--catalog`.
