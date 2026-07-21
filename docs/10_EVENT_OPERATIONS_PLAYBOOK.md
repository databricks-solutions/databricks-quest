# 10 — Event Operations Playbook

## Purpose

This playbook defines how a Databricks Quest GameDay is planned, configured, dry-run, delivered, and followed up.

## Event lifecycle

```text
Plan → Configure → Dry-run → Launch → Play → Freeze → Debrief → Export → Reset
```

## 1. Plan

### Define event purpose

Choose one:

- internal enablement
- customer workshop
- hunter account activation
- conference/event booth
- partner enablement
- product launch enablement

### Define success criteria

Examples:

- 80% of teams complete the first three quests
- every team creates a governed table and AI/BI dashboard
- at least five prospect champions complete the GenAI challenge
- collect top blockers for future enablement

### Choose quest pack

Start with one of:

- Lakehouse Foundations
- AI/BI GameDay
- GenAI/RAG on Databricks
- Unity Catalog Governance
- Lakeflow Data Engineering
- Cost/Performance Optimization

## 2. Configure

Host actions:

1. Deploy/update Databricks Quest.
2. Import quest pack manifest.
3. Create event.
4. Set event time window.
5. Configure scoring rules.
6. Import participants.
7. Create or auto-generate teams.
8. Configure team resource namespace.
9. Run environment prechecks.
10. Seed required datasets.

## 3. Dry-run

A dry-run is mandatory before external/customer events.

Dry-run checklist:

- Quest pack lints cleanly.
- All validators parse correctly.
- SQL Warehouse is reachable.
- App can connect to Lakebase.
- Team schemas/catalogs can be created.
- Seed data exists.
- Sample pass/fail submissions behave correctly.
- Leaderboard updates.
- Host controls work.
- Reset works.

## 4. Launch

Opening briefing agenda:

1. Welcome and event objective.
2. Story narrative.
3. Team format.
4. Rules and scoring.
5. Hint penalty explanation.
6. Support model.
7. Leaderboard and final reveal.
8. Start event.

Recommended rules:

- Teams may use documentation and AI assistants unless event rules say otherwise.
- Hints are allowed but cost points.
- Host decisions are final.
- Do not intentionally disrupt other teams.
- Use only assigned team resources.
- Submissions after freeze do not count.

## 5. Play

Host actions during gameplay:

- monitor validation queue
- monitor teams with no progress
- issue announcements
- identify broken validators
- handle manual reviews
- prepare debrief examples
- avoid over-helping unless teams are blocked

Support pattern:

- Ask questions before giving answers.
- Use hints rather than direct steps.
- Encourage team role division.
- Keep energy high with leaderboard moments.

## 6. Freeze

At event end:

1. Host clicks Freeze.
2. New submissions are blocked.
3. Pending validations finish.
4. Manual adjudications are applied.
5. Final leaderboard is calculated.

## 7. Debrief

Debrief structure:

1. Final leaderboard reveal.
2. Winning team approach.
3. Common failure patterns.
4. Databricks best-practice recap.
5. Business outcome mapping.
6. Next steps.

For hunter accounts, explicitly connect outcomes to deal strategy:

- Which pains did they experience?
- Which Databricks capabilities unlocked progress?
- Who emerged as champions?
- Which use cases need follow-up?

## 8. Export

Export artifacts:

- event summary
- team leaderboard
- quest completion matrix
- validation failure summary
- hint usage
- team participation
- skill coverage
- top blockers
- sales follow-up notes

## 9. Reset

After event:

- archive event
- export results
- reset or drop team schemas
- revoke temporary grants
- stop unused warehouses/jobs
- clean generated endpoints/resources
- preserve audit tables

## Multi-workspace federation (large lab events)

For large lab events where every attendee gets their own provisioned Databricks
workspace, deploy in **federated mode**: one **master** app on the admin
workspace owns the shared Lakebase, and each attendee workspace runs a **child**
app that writes to it directly over Postgres. Players play in their own child
app; the master is the host console. See
`adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md`.

### Setup sequence

1. **Deploy the master** on the admin workspace and create the event:

   ```bash
   ./deploy.sh --role master --event anz-gameday
   ```

   The master provisions the shared INSERT-only event-writer credential and
   prints it (plus an example child deploy command) in the success banner.
   Distribute the credential and master Lakebase host to the child deploys.

2. **Deploy a child** into each attendee workspace, pointing at the master:

   ```bash
   ./deploy.sh --role child \
     --master-lakebase-host <master-host> \
     --master-lakebase-token <event-writer-credential> \
     --event anz-gameday \
     --workspace-id ws-anzgt-01
   ```

   Children skip local Lakebase provisioning and migrations; they connect to the
   already-migrated shared DB and check themselves in on startup. (If workspace
   provisioning templates the deploy, pass a unique `--workspace-id` per
   attendee.)

3. **Import the roster** on the master host console (or via
   `POST /api/host/events/{event_id}/roster/import`). The CSV maps each
   `workspace_id` + `labuser+{n}@awsbricks.com` to a real name and team. Lab
   environments create generic `labuser+{n}@awsbricks.com` users, so this map is
   what turns anonymous lab users into named teams on the leaderboard.

### During the event

- Watch the **Workspaces** health panel for children that have stopped checking
  in or stopped writing scores.
- Watch the **Unmapped identities** panel for attendees writing scores who are
  not on the roster yet. Nothing is lost — add them to the roster CSV and
  **re-import** (idempotent) to attribute their points retroactively.
- The event-wide leaderboard is identical on the master and on every child, so
  attendees see global standings and their own team's rank from inside their own
  workspace.

### Federation pre-event checklist

- Master deployed and event created; event-writer credential captured.
- A child app deployed in each attendee workspace, all pointing at the master.
- Child apps appear in the master Workspaces panel (startup check-in succeeded).
- Roster CSV imported; **Unmapped identities** is empty after a sample write.
- A sample child submission appears on the master leaderboard under the right
  team.
- Writer credential verified as INSERT-only (`python scripts/federation_spike.py`).

## Event staffing model

### Small event: 10–25 people

- 1 host
- 1 technical facilitator

### Medium event: 25–75 people

- 1 host
- 2–3 technical facilitators
- 1 ops person

### Large event: 75–300 people

- 1 lead host
- 1 event ops lead
- 4–8 facilitators
- 1 platform admin
- 1 support channel moderator

## Recommended event durations

| Format | Duration | Use case |
|---|---:|---|
| Lightning GameDay | 60–90 min | conference or exec workshop |
| Standard GameDay | 2–3 hours | customer enablement / field workshop |
| Deep Dive GameDay | half-day | technical account activation |
| Enablement Tournament | multi-day | internal certification or community event |

## Pre-event technical checklist

- Databricks App is deployed.
- Lakebase is reachable.
- SQL Warehouse is running or serverless available.
- Quest catalog/schema exists.
- System tables enabled if pack needs telemetry.
- Event resource catalog/schema exists.
- Validators dry-run successfully.
- Teams imported.
- Host users have admin rights.
- Cleanup script is tested.

## Event communication template

```text
Welcome to Databricks Quest GameDay.

Today you are part of a team solving a realistic data and AI challenge on Databricks. This is not a step-by-step lab. Your team will decide how to solve the missions, submit your work for validation, and earn points as you progress.

Use the Quest app to view missions, request hints, validate your work, and track the leaderboard. Hints cost points. The event will freeze at the end of the timer, and the final leaderboard will be revealed during the debrief.
```
