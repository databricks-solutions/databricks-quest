# 07 — Data Model

## Data model principles

1. **Lakebase is operational state.** Events, attempts, validation results, and leaderboards need transactional low-latency reads/writes.
2. **Delta is analytics and audit.** Append-only facts should be synced to Delta for reporting, joins with system tables, and historical analysis.
3. **Scoring is append-only.** Do not mutate points without writing a compensating scoring event.
4. **Quest pack versions are immutable once an event starts.** This preserves fairness.
5. **Event state is scoped.** Every row must include `event_id` where applicable.

## Lakebase operational tables

### quest_packs

```sql
CREATE TABLE quest_packs (
  pack_id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  description TEXT,
  owner TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  created_by TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### quest_pack_versions

```sql
CREATE TABLE quest_pack_versions (
  pack_version_id TEXT PRIMARY KEY,
  pack_id TEXT NOT NULL REFERENCES quest_packs(pack_id),
  version TEXT NOT NULL,
  manifest_json JSONB NOT NULL,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  imported_by TEXT,
  imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(pack_id, version)
);
```

### quests

```sql
CREATE TABLE quests (
  quest_id TEXT PRIMARY KEY,
  pack_version_id TEXT NOT NULL REFERENCES quest_pack_versions(pack_version_id),
  slug TEXT NOT NULL,
  title TEXT NOT NULL,
  narrative TEXT,
  category TEXT,
  difficulty TEXT,
  sort_order INT DEFAULT 0,
  base_points INT DEFAULT 0,
  unlock_rule_json JSONB,
  facilitator_notes TEXT,
  UNIQUE(pack_version_id, slug)
);
```

### quest_tasks

```sql
CREATE TABLE quest_tasks (
  task_id TEXT PRIMARY KEY,
  quest_id TEXT NOT NULL REFERENCES quests(quest_id),
  slug TEXT NOT NULL,
  title TEXT NOT NULL,
  objective TEXT NOT NULL,
  instructions_md TEXT,
  success_criteria_md TEXT,
  points INT NOT NULL DEFAULT 0,
  sort_order INT DEFAULT 0,
  validation_mode TEXT DEFAULT 'auto',
  scoring_json JSONB,
  metadata_json JSONB,
  UNIQUE(quest_id, slug)
);
```

### task_validators

```sql
CREATE TABLE task_validators (
  validator_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES quest_tasks(task_id),
  type TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'sync',
  config_json JSONB NOT NULL,
  expected_json JSONB,
  timeout_seconds INT DEFAULT 30,
  sort_order INT DEFAULT 0,
  enabled BOOLEAN DEFAULT TRUE
);
```

### task_hints

```sql
CREATE TABLE task_hints (
  hint_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES quest_tasks(task_id),
  sort_order INT NOT NULL,
  title TEXT,
  body_md TEXT NOT NULL,
  penalty_points INT DEFAULT 0
);
```

### events

```sql
CREATE TABLE events (
  event_id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  description TEXT,
  pack_version_id TEXT NOT NULL REFERENCES quest_pack_versions(pack_version_id),
  mode TEXT NOT NULL DEFAULT 'gameday',
  status TEXT NOT NULL DEFAULT 'draft',
  starts_at TIMESTAMP,
  ends_at TIMESTAMP,
  timezone TEXT DEFAULT 'UTC',
  scoring_frozen_at TIMESTAMP,
  created_by TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  config_json JSONB
);
```

Event statuses:

```text
draft → ready → active → paused → frozen → completed → archived
```

### event_hosts

```sql
CREATE TABLE event_hosts (
  event_id TEXT NOT NULL REFERENCES events(event_id),
  user_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'host',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(event_id, user_id)
);
```

### teams

```sql
CREATE TABLE teams (
  team_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  name TEXT NOT NULL,
  display_name TEXT,
  color TEXT,
  team_catalog TEXT,
  team_schema TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(event_id, name)
);
```

### participants

```sql
CREATE TABLE participants (
  participant_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  user_id TEXT NOT NULL,
  display_name TEXT,
  email TEXT,
  role TEXT DEFAULT 'player',
  registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP,
  UNIQUE(event_id, user_id)
);
```

### team_members

```sql
CREATE TABLE team_members (
  team_id TEXT NOT NULL REFERENCES teams(team_id),
  participant_id TEXT NOT NULL REFERENCES participants(participant_id),
  joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(team_id, participant_id)
);
```

### task_attempts

```sql
CREATE TABLE task_attempts (
  attempt_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  team_id TEXT NOT NULL REFERENCES teams(team_id),
  task_id TEXT NOT NULL REFERENCES quest_tasks(task_id),
  submitted_by TEXT NOT NULL,
  submission_json JSONB,
  status TEXT NOT NULL DEFAULT 'submitted',
  submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  error_message TEXT
);
```

### validation_results

```sql
CREATE TABLE validation_results (
  validation_result_id TEXT PRIMARY KEY,
  attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id),
  validator_id TEXT NOT NULL REFERENCES task_validators(validator_id),
  status TEXT NOT NULL,
  score_delta INT DEFAULT 0,
  public_message TEXT,
  private_message TEXT,
  evidence_json JSONB,
  started_at TIMESTAMP,
  completed_at TIMESTAMP
);
```

### scoring_events

```sql
CREATE TABLE scoring_events (
  scoring_event_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  team_id TEXT,
  user_id TEXT,
  quest_id TEXT,
  task_id TEXT,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  points_delta INT NOT NULL,
  reason TEXT NOT NULL,
  created_by TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  idempotency_key TEXT UNIQUE
);
```

### hints_taken

```sql
CREATE TABLE hints_taken (
  event_id TEXT NOT NULL REFERENCES events(event_id),
  team_id TEXT NOT NULL REFERENCES teams(team_id),
  task_id TEXT NOT NULL REFERENCES quest_tasks(task_id),
  hint_id TEXT NOT NULL REFERENCES task_hints(hint_id),
  taken_by TEXT NOT NULL,
  penalty_points INT DEFAULT 0,
  taken_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(event_id, team_id, hint_id)
);
```

### announcements

```sql
CREATE TABLE announcements (
  announcement_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  title TEXT NOT NULL,
  body_md TEXT NOT NULL,
  severity TEXT DEFAULT 'info',
  created_by TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### manual_adjustments

```sql
CREATE TABLE manual_adjustments (
  adjustment_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  team_id TEXT,
  user_id TEXT,
  task_id TEXT,
  points_delta INT NOT NULL,
  reason TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### event_audit_log

```sql
CREATE TABLE event_audit_log (
  audit_id TEXT PRIMARY KEY,
  event_id TEXT,
  actor_user_id TEXT,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  payload_json JSONB,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### event_resources (migration 005, PR08)

Registry of the per-team Databricks resources an event has provisioned, for the
host resource-health view. Operational state only — the authoritative safety
guard is `services/namespace.py`, which refuses any target outside the event's
computed namespace regardless of what is recorded here. A row is upserted on
bootstrap (`active`/`failed`) and flipped to `removed` on reset.

```sql
CREATE TABLE event_resources (
  resource_id   TEXT PRIMARY KEY,
  event_id      TEXT NOT NULL REFERENCES events(event_id),
  team_id       TEXT REFERENCES teams(team_id),
  resource_type TEXT NOT NULL,            -- 'catalog' | 'schema'
  fqn           TEXT NOT NULL,            -- catalog.schema target
  status        TEXT NOT NULL DEFAULT 'pending', -- pending|active|failed|removed
  message       TEXT,
  created_by    TEXT,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (event_id, fqn)
);
```

The per-team target FQN is `team_catalog`/`team_schema` if set on the team, else
`quest_<event-slug>.<schema_prefix><team-name>`. These are the same values that
fill the `${team_catalog}`/`${team_schema}` validator slots.

## Derived views

### team_scores

```sql
CREATE VIEW team_scores AS
SELECT
  event_id,
  team_id,
  SUM(points_delta) AS total_points,
  COUNT(*) AS scoring_events,
  MAX(created_at) AS last_scored_at
FROM scoring_events
GROUP BY event_id, team_id;
```

### event_leaderboard

```sql
CREATE VIEW event_leaderboard AS
SELECT
  s.event_id,
  s.team_id,
  t.display_name,
  s.total_points,
  RANK() OVER (PARTITION BY s.event_id ORDER BY s.total_points DESC, s.last_scored_at ASC) AS rank,
  s.last_scored_at
FROM team_scores s
JOIN teams t ON s.team_id = t.team_id;
```

### task_completion_status

```sql
CREATE VIEW task_completion_status AS
SELECT
  a.event_id,
  a.team_id,
  a.task_id,
  MAX(CASE WHEN a.status = 'passed' THEN 1 ELSE 0 END) AS completed,
  COUNT(*) AS attempts,
  MAX(a.completed_at) AS last_attempt_at
FROM task_attempts a
GROUP BY a.event_id, a.team_id, a.task_id;
```

## Multi-workspace federation (ADR_006)

For large events we run **one app per attendee workspace**, all writing to a
single shared Lakebase attached to the master workspace. The schema is the same
for standalone, master, and child roles — federation adds nullable columns and
two tables, so standalone behaviour is unchanged. See
`adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md` and
`app/migrations/002_federation.sql`.

### Federation columns (migration 002)

`workspace_id TEXT` (nullable) is added to `scoring_events`, `task_attempts`,
`validation_results`, `hints_taken`, and `participants`. Federated rows stamp it
(and write `user_id = labuser+{n}@awsbricks.com`); standalone rows leave it
`NULL`. The unique `scoring_events.idempotency_key` is made deterministic per
`(workspace_id, source)` so a child retry never double-awards.

### event_workspaces

Child presence registry. Each child app upserts one row on startup (no outbox,
no ingest API). The master host console reads it for the workspace-health panel.

```sql
CREATE TABLE event_workspaces (
  workspace_id   TEXT PRIMARY KEY,
  event_id       TEXT,
  event_slug     TEXT,
  workspace_host TEXT,
  app_url        TEXT,
  app_version    TEXT,
  status         TEXT NOT NULL DEFAULT 'active',
  registered_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_seen_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### participant_identity_map

Central mapping of `(event_id, workspace_id, lab_user_email)` → real person and
team. The host roster import populates it; the leaderboard view resolves
federated `scoring_events` through it. `source` records provenance (`roster`,
`self_claim`, `pending`).

```sql
CREATE TABLE participant_identity_map (
  event_id       TEXT NOT NULL,
  workspace_id   TEXT NOT NULL,
  lab_user_email TEXT NOT NULL,
  participant_id TEXT,
  team_id        TEXT,
  real_email     TEXT,
  display_name   TEXT,
  source         TEXT NOT NULL DEFAULT 'roster',
  created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (event_id, workspace_id, lab_user_email)
);
```

### Identity-resolving views (migration 002)

`team_scores` is redefined to resolve a team via
`COALESCE(scoring_events.team_id, participant_identity_map.team_id)` — standalone
rows keep their direct `team_id`, federated rows resolve through the identity
map, and rows that resolve to no team are excluded. `event_leaderboard` keeps the
same shape (so it works identically in every role) but now transitively spans
every workspace. A new `unmapped_identities` view surfaces federated
`scoring_events` not yet on the roster for host reconciliation — nothing is lost;
points attribute the moment the roster is (re-)imported.

```sql
CREATE OR REPLACE VIEW team_scores AS
SELECT se.event_id,
       COALESCE(se.team_id, pim.team_id) AS team_id,
       SUM(se.points_delta) AS total_points,
       COUNT(*)             AS scoring_events,
       MAX(se.created_at)   AS last_scored_at
FROM scoring_events se
LEFT JOIN participant_identity_map pim
       ON pim.event_id = se.event_id
      AND pim.workspace_id = se.workspace_id
      AND pim.lab_user_email = se.user_id
WHERE COALESCE(se.team_id, pim.team_id) IS NOT NULL
GROUP BY se.event_id, COALESCE(se.team_id, pim.team_id);
```

### Shared writer credential

Children authenticate to the master Lakebase with a single shared, INSERT-only
event-writer Postgres role (provisioned by `deploy.sh --role master`). It holds
`INSERT` on the four fact tables + `SELECT` on the read tables/views and nothing
else, so a leaked child credential cannot mutate or delete scores. Standalone and
master apps continue to use workspace-identity OAuth.

## Delta analytics tables

Sync these Lakebase tables to Delta as append-only facts or snapshots:

- `event_fact`
- `team_fact`
- `participant_fact`
- `task_attempt_fact`
- `validation_result_fact`
- `scoring_event_fact`
- `hint_usage_fact`
- `leaderboard_snapshot_fact`
- `event_audit_fact`

## Migration approach

PR1 should add migrations and a migration runner.

Recommended migration runner:

```bash
python app/migrations/run_migrations.py --lakebase-host ... --lakebase-db ...
```

For Databricks deploy:

- run migrations during `deploy.sh`
- run migrations idempotently before app startup
- record applied migrations in `schema_migrations`

### PR01 implementation status

Implemented in PR01:

- `app/db.py` — centralized Lakebase connection handling (shared by the app and
  the migration runner). Supports workspace-identity OAuth (app) and explicit
  credentials (deploy).
- `app/migrations/001_gameday_core.sql` — all operational tables above plus the
  `team_scores`, `event_leaderboard`, and `task_completion_status` views. Every
  statement is idempotent (`CREATE ... IF NOT EXISTS` / `CREATE OR REPLACE`).
- `app/migrations/run_migrations.py` — idempotent runner. Ensures
  `schema_migrations`, applies only unrecorded `*.sql` files (ordered by
  filename), and records each. Backends: psycopg2 (preferred), psql CLI
  fallback (no local psycopg2 needed), or workspace OAuth when no password is
  supplied. Safe to run repeatedly.
- `deploy.sh` runs the migrations against Lakebase right after provisioning,
  before the service-principal `GRANT`, so new tables are readable by the app.
- `/api/health` reports `migrations_applied` / `migrations_count`.

Repository/service stubs (`app/repositories/`, `app/services/audit.py`) expose
the read paths over these tables; mutation paths are deferred to later PRs.

### schema_migrations

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  description TEXT,
  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
