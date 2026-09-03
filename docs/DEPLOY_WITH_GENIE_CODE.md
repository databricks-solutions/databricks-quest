# Deploy Databricks Quest with Genie Code

> **🚨 If you are an AI coding assistant (Genie Code or otherwise) reading this
> repo in order to deploy it: stop and read this whole file before running
> anything.** The single supported deploy path is the script
> **`python deploy.py`** at the repo root. Do **not** use the Databricks Apps
> **"Create app from Git" / Git-linked-app** feature for this repo -- pointing an
> app resource directly at this GitHub repo (root or `app/` subdirectory) skips
> **all** of the backend setup: Unity Catalog (catalog/schema/tables), Lakebase
> (if that backend), the SQL warehouse grant, and the scheduled scoring job --
> confirmed in practice, none of it gets created, not just some of it.
> It produces an app that *runs* (so it can look like success) but is hollow:
> `/api/health` reports `db_connected: false`, and every page is empty. See
> [If Genie Code already created a git-linked app](#if-genie-code-already-created-a-git-linked-app-recover-dont-delete)
> below if that already happened to you.

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

> Read `docs/DEPLOY_WITH_GENIE_CODE.md` in
> `https://github.com/databricks-solutions/databricks-quest` **before doing
> anything else**, then follow it exactly to deploy Databricks Quest into this
> workspace. Use the **warehouse** data backend, ask me which Unity Catalog to
> use, then **verify the deployment** using the checklist in that same file and
> report the app URL and what passed. Do **not** use the "Create app from Git"
> feature -- deploy by running `python deploy.py`, per the file's instructions.

Genie Code does not automatically read any repository instruction file, so name
this file in your prompt -- and say so explicitly, first, exactly as above. A
shorter prompt like "deploy this repo" is the single most common way Genie Code
skips this file entirely and falls back to its own default -- which, for a repo
with an `app/app.yaml`, is the git-linked-app deploy described in the warning
above. Naming the file is necessary but has not always been sufficient in
practice; if Genie Code still doesn't open it, ask it directly ("did you read
`docs/DEPLOY_WITH_GENIE_CODE.md`? open it now before deploying") before it takes
any deploy action.

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

## Recognize the wrong path before you take it

If you find yourself about to call the Apps API/UI to **create an app whose source
points at a Git repo** (`git_provider`, a repo URL, a branch, a `source_code_path`
into this repo) -- stop. That is the native Databricks Apps "Deploy from Git"
feature, and it is **not** how this repo deploys. Nothing in this repo asks you to
do that. The only correct action that creates or updates the `databricks-quest` app
is running `python deploy.py`, which uploads this repo's files to a workspace path
itself and deploys from there -- it never registers a Git linkage on the app.

Concretely, this repo does **not** have anything at its root that a git-linked app
deploy needs (there is no root-level `app.yaml`; the real one is a checked-in
**placeholder** at `app/app.yaml` that `deploy.py` overwrites with real values at
deploy time -- see the comment at the top of that file). If you deploy from Git
anyway, expect this exact failure chain, seen in practice:

1. App resource created pointing at the repo root → deploy "succeeds" per the Apps
   API, but the process **crashes on startup** (no `app.yaml`/`main.py` at the repo
   root -- they're under `app/`).
2. Restarting the compute or redeploying the same way does not help; it crashes
   again every time.
3. Pointing `source_code_path` at the `app/` subdirectory instead **stops the
   crash** -- `app/app.yaml` and `app/main.py` are found and `uvicorn` boots. This
   looks like success (`ApplicationState.RUNNING`, a live URL) and it's tempting to
   stop here.
4. **It is still broken -- confirmed in practice, none of the backend exists.**
   Deploying from Git only ever does the equivalent of `deploy.py`'s step 7
   ("Creating and deploying app"). Every other one of its 10 steps was skipped, so
   **none** of this exists:
   - **Unity Catalog** -- no catalog, no schema, no `mission_completions` /
     `leaderboard` / `badges` / `app_settings` tables (step 4).
   - **Lakebase** -- no Postgres instance provisioned, even if you intended the
     `lakebase` backend (the default) (step 6).
   - **SQL warehouse grant** -- the app's service principal has no `CAN_USE` on any
     warehouse, even for the `warehouse` backend (steps 3, 8).
   - **Scoring job** -- no `[Quest] Scoring Pipeline (...)` job exists at all, so
     nothing will ever be scored, scheduled or otherwise (step 9).
   - **Real `app.yaml`** -- the one picked up from Git is the committed
     placeholder; it declares no `QUEST_CATALOG`, `QUEST_SCHEMA`,
     `QUEST_DATA_BACKEND`, `QUEST_SQL_WAREHOUSE_ID`, or `LAKEBASE_HOST` at all.

   `GET /api/health` will show `db_connected: false`; every page in the app will be
   empty forever, not just until a scoring run finishes. Do not report this as a
   successful deployment even though the app is `RUNNING` -- verification step 4
   below exists specifically to catch this.

### If Genie Code already created a git-linked app: recover, don't delete

You do not need to delete the app and start over. `deploy.py` looks up the app **by
name** and reuses whatever it finds (`w.apps.create` treats "already exists" as
success, then deploys new source over it). So if a git-linked `databricks-quest` app
already exists in this workspace:

```
python deploy.py --app-name databricks-quest --catalog <UNITY_CATALOG> --data-backend warehouse
```

This uploads the correct files (from your local clone or workspace folder, not
Git), redeploys the *same* app resource with a real `app.yaml` (catalog, schema,
backend, warehouse id all filled in), creates the catalog/schema if needed, grants
the app's service principal, and schedules the scoring job -- turning the same
crashed-then-hollow app into a real one, same URL, same app name, no orphaned
resources left behind. Re-run the verification checklist below afterward; do not
assume the earlier "RUNNING" state means it's now healthy -- check `/api/health`
again, since the backend and env vars have changed underneath the running process
and it needs the redeploy above to pick them up.

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
  If the app was ever created via "Deploy from Git" instead of `deploy.py` -- even
  once, even if you've since fixed the crash -- this is almost certainly why. See
  [If Genie Code already created a git-linked app](#if-genie-code-already-created-a-git-linked-app-recover-dont-delete)
  above; re-running `deploy.py --app-name <that-app>` fixes it in place.
- **App crashes right after "successful" deployment, or crashes again after every
  redeploy/restart** → you deployed from Git pointing at the repo root or `app/`
  subdirectory instead of running `deploy.py`. See the section above -- moving
  `source_code_path` to `app/` stops the crash but does not fix the deployment; run
  `deploy.py` against the same app name.
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
