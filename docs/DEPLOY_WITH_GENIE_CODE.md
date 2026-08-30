# Deploy Databricks Quest with Genie Code

A guide for deploying **Databricks Quest** into your own Databricks workspace using
**Genie Code**, the AI coding assistant built into the workspace. It gives Genie
Code the goal, the few things it must get right, and -- most importantly -- a way to
**verify its own work**. The mechanics of running the deploy are left to Genie Code;
this is deliberately high-level.

Quest runs in **Adoption Mode**: a scoring job reads your workspace's system tables
every 4 hours, and a Databricks App shows each user their missions, points, badges,
and the leaderboard.

## Give Genie Code this instruction

Open Genie Code in your workspace and paste something like:

> Clone `https://github.com/databricks-solutions/databricks-quest` and deploy
> Databricks Quest into this workspace. Use the **warehouse** data backend, ask me
> which Unity Catalog to use, then **verify the deployment** using the checklist in
> `docs/DEPLOY_WITH_GENIE_CODE.md` and report the app URL and what passed.

Genie Code does not automatically read any repository instruction file, so name this
file in your prompt.

## The goal

A running **Databricks App** named `databricks-quest`, backed by a scored data set,
reachable at its app URL, with the 4-hourly scoring job scheduled. That's success --
the verification checklist below defines it precisely.

## How to deploy (you drive this)

The entire deploy is one script at the repo root -- **`deploy.py`** -- that runs
through the Databricks SDK (no bash, no `psql`, no Terraform), so it works from a
terminal or from a notebook cell inside the workspace. The single command is:

```
python deploy.py --catalog <UNITY_CATALOG> --data-backend warehouse
```

Work out the surrounding mechanics for the environment you're in -- clone or Git
folder, `pip install -r requirements.txt`, and authentication (`databricks auth
login`, a `--profile`, or `DATABRICKS_HOST`/`DATABRICKS_TOKEN`; inside a notebook use
the workspace's own credentials). The exact commands, every flag, and the Windows
path are in **[WINDOWS_DEPLOY.md](WINDOWS_DEPLOY.md)**; the full deployment guide,
including a manual step-by-step path, is in **[../SETUP.md](../SETUP.md)**.

A few things to get right:

- **Data backend:** prefer `--data-backend warehouse` -- it reads the scored Delta
  tables through a SQL warehouse with no Lakebase/Postgres to provision or sync. The
  default (`lakebase`) provisions a Postgres instance and adds ~5 minutes.
- **Catalog:** `--catalog` must be a Unity Catalog the deploying identity can create
  a schema in (`CREATE SCHEMA`, or `CREATE CATALOG` for a new one). `deploy.py` runs
  a pre-flight check and stops with the exact `GRANT` if it can't -- surface that to
  the user rather than guessing.
- **Unattended runs:** add `--non-interactive` (then `--catalog` is required).
- **Re-runs are safe:** `deploy.py` reuses the app, warehouse, catalog, and job, so
  if a check below fails you can fix the cause and run it again.

## Verify the deployment (do this -- don't stop at "the script finished")

Run these checks in order. Each has a clear pass signal. Do not report success until
they pass; report exactly which ones are green and which are still pending.

1. **Authenticated to the right workspace.** `databricks current-user me` returns the
   expected user and workspace host. If not, fix auth before anything else.
2. **Deploy exited cleanly.** `deploy.py` finished without error and printed an
   **app URL**. Capture that URL.
3. **App is running.** `databricks apps get databricks-quest` shows the app's
   compute active and its latest deployment succeeded (not crashed or still
   deploying), and reports the app URL and service principal.
4. **App self-reports healthy.** Fetch **`GET /api/health`**. Pass = top-level
   `status` is `"ok"` and `db_connected` is `true` -- the app reached its active
   data backend (warehouse or Lakebase) and completed a round-trip, reported as
   `db_latency_ms`. The `checks` block breaks it down by subsystem (`migrations`,
   `validators`, `scoring`, `sql_warehouse`, and the active-backend round-trip under
   `lakebase`). A `degraded` status means the app can't reach its backend -- treat
   that as a failed deploy. This endpoint is the fastest way to tell a good deploy
   from a broken one.
5. **Scoring job scheduled and started.** A job named
   `[Quest] Scoring Pipeline (databricks-quest)` exists with an **UNPAUSED 4-hour
   schedule**, and its first run has been triggered. Check that the latest run
   succeeded or is in progress (tasks: `roundtrip_attestations` → `run_scoring` →
   `sync_to_lakebase` / `warm_warehouse`).
6. **Data shows up once scoring finishes.** The first run takes ~10-20 minutes. When
   it completes, `GET /api/profile`, `/api/missions`, and `/api/leaderboard` return
   data -- or equivalently the Delta tables `<catalog>.quest.leaderboard` and
   `<catalog>.quest.mission_completions` have rows. An **empty app right after
   deploy is expected**, not a failure; re-check after the first run.
7. **Idempotent (optional).** Re-running the same `deploy.py` command reuses existing
   resources and does not create a second app or job.

**Report back:** the app URL, which backend is active, whether the first scoring run
has finished, and any check not yet green.

## If a check fails, self-diagnose

- **Auth (1) fails** → re-run `databricks auth login` or pass `--profile` /
  `DATABRICKS_HOST`+`DATABRICKS_TOKEN`; confirm with `databricks current-user me`.
- **Deploy stops on catalog permissions (2)** → the identity lacks `CREATE SCHEMA` /
  `CREATE CATALOG`. Use a catalog you can already create schemas in, or ask an admin
  to grant it (the exact `GRANT` is in `../SETUP.md`). Then re-run.
- **App RUNNING but `/api/health` shows a backend error (3-4)** → check the app's
  env (`LAKEBASE_HOST`/`LAKEBASE_DB`) and that the app service principal has its
  grants; for the warehouse backend confirm the SQL warehouse exists and is usable.
- **App loads but shows zeros (6)** → the first scoring run hasn't finished. Find the
  job run under **Workflows**, wait for it, then reload.
- **Anything deeper** → `docs/17_TROUBLESHOOTING.md` covers both backends symptom by
  symptom, and `../SETUP.md` has the full manual path.

## One honest note on execution

Genie Code is the AI coding assistant in the Databricks workspace; exactly what it
can run directly (a shell, the `databricks` CLI, `python deploy.py`) isn't formally
specified in the docs and can vary. If it can't run a step itself, a workspace
notebook is a reliable place to do it (`%pip`, `%sh`, Python cells), or a human can
run the one command from a terminal. `deploy.py` only needs the Databricks SDK and a
way to authenticate to the workspace.
