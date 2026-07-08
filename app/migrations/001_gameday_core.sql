-- description: GameDay core operational tables (quest packs, events, teams, attempts, scoring)
--
-- Adds the Event Mode data foundation to Lakebase. Adoption-mode tables
-- (mission_completions, user_profile_snapshot, leaderboard, badges, ...) are
-- intentionally left untouched. Every statement is idempotent so this file is
-- safe to run repeatedly; the migration runner also records applied versions
-- in schema_migrations and no-ops on re-run.

-- ── Quest pack catalog ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS quest_packs (
  pack_id     TEXT PRIMARY KEY,
  slug        TEXT NOT NULL UNIQUE,
  title       TEXT NOT NULL,
  description TEXT,
  owner       TEXT,
  status      TEXT NOT NULL DEFAULT 'draft',
  created_by  TEXT,
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quest_pack_versions (
  pack_version_id TEXT PRIMARY KEY,
  pack_id         TEXT NOT NULL REFERENCES quest_packs(pack_id),
  version         TEXT NOT NULL,
  manifest_json   JSONB NOT NULL,
  content_hash    TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'draft',
  imported_by     TEXT,
  imported_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (pack_id, version)
);

CREATE TABLE IF NOT EXISTS quests (
  quest_id          TEXT PRIMARY KEY,
  pack_version_id   TEXT NOT NULL REFERENCES quest_pack_versions(pack_version_id),
  slug              TEXT NOT NULL,
  title             TEXT NOT NULL,
  narrative         TEXT,
  category          TEXT,
  difficulty        TEXT,
  sort_order        INT DEFAULT 0,
  base_points       INT DEFAULT 0,
  unlock_rule_json  JSONB,
  facilitator_notes TEXT,
  UNIQUE (pack_version_id, slug)
);

CREATE TABLE IF NOT EXISTS quest_tasks (
  task_id             TEXT PRIMARY KEY,
  quest_id            TEXT NOT NULL REFERENCES quests(quest_id),
  slug                TEXT NOT NULL,
  title               TEXT NOT NULL,
  objective           TEXT NOT NULL,
  instructions_md     TEXT,
  success_criteria_md TEXT,
  points              INT NOT NULL DEFAULT 0,
  sort_order          INT DEFAULT 0,
  validation_mode     TEXT DEFAULT 'auto',
  scoring_json        JSONB,
  metadata_json       JSONB,
  UNIQUE (quest_id, slug)
);

CREATE TABLE IF NOT EXISTS task_validators (
  validator_id    TEXT PRIMARY KEY,
  task_id         TEXT NOT NULL REFERENCES quest_tasks(task_id),
  type            TEXT NOT NULL,
  mode            TEXT NOT NULL DEFAULT 'sync',
  config_json     JSONB NOT NULL,
  expected_json   JSONB,
  timeout_seconds INT DEFAULT 30,
  sort_order      INT DEFAULT 0,
  enabled         BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS task_hints (
  hint_id        TEXT PRIMARY KEY,
  task_id        TEXT NOT NULL REFERENCES quest_tasks(task_id),
  sort_order     INT NOT NULL,
  title          TEXT,
  body_md        TEXT NOT NULL,
  penalty_points INT DEFAULT 0
);

-- ── Events, hosts, teams, participants ───────────────────────────────────────

CREATE TABLE IF NOT EXISTS events (
  event_id          TEXT PRIMARY KEY,
  slug              TEXT NOT NULL UNIQUE,
  title             TEXT NOT NULL,
  description       TEXT,
  pack_version_id   TEXT NOT NULL REFERENCES quest_pack_versions(pack_version_id),
  mode              TEXT NOT NULL DEFAULT 'gameday',
  status            TEXT NOT NULL DEFAULT 'draft',
  starts_at         TIMESTAMP,
  ends_at           TIMESTAMP,
  timezone          TEXT DEFAULT 'UTC',
  scoring_frozen_at TIMESTAMP,
  created_by        TEXT,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  config_json       JSONB
);

CREATE TABLE IF NOT EXISTS event_hosts (
  event_id   TEXT NOT NULL REFERENCES events(event_id),
  user_id    TEXT NOT NULL,
  role       TEXT NOT NULL DEFAULT 'host',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (event_id, user_id)
);

CREATE TABLE IF NOT EXISTS teams (
  team_id      TEXT PRIMARY KEY,
  event_id     TEXT NOT NULL REFERENCES events(event_id),
  name         TEXT NOT NULL,
  display_name TEXT,
  color        TEXT,
  team_catalog TEXT,
  team_schema  TEXT,
  created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (event_id, name)
);

CREATE TABLE IF NOT EXISTS participants (
  participant_id TEXT PRIMARY KEY,
  event_id       TEXT NOT NULL REFERENCES events(event_id),
  user_id        TEXT NOT NULL,
  display_name   TEXT,
  email          TEXT,
  role           TEXT DEFAULT 'player',
  registered_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_seen_at   TIMESTAMP,
  UNIQUE (event_id, user_id)
);

CREATE TABLE IF NOT EXISTS team_members (
  team_id        TEXT NOT NULL REFERENCES teams(team_id),
  participant_id TEXT NOT NULL REFERENCES participants(participant_id),
  joined_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (team_id, participant_id)
);

-- ── Attempts, validation, scoring ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS task_attempts (
  attempt_id      TEXT PRIMARY KEY,
  event_id        TEXT NOT NULL REFERENCES events(event_id),
  team_id         TEXT NOT NULL REFERENCES teams(team_id),
  task_id         TEXT NOT NULL REFERENCES quest_tasks(task_id),
  submitted_by    TEXT NOT NULL,
  submission_json JSONB,
  status          TEXT NOT NULL DEFAULT 'submitted',
  submitted_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  started_at      TIMESTAMP,
  completed_at    TIMESTAMP,
  error_message   TEXT
);

CREATE TABLE IF NOT EXISTS validation_results (
  validation_result_id TEXT PRIMARY KEY,
  attempt_id           TEXT NOT NULL REFERENCES task_attempts(attempt_id),
  validator_id         TEXT NOT NULL REFERENCES task_validators(validator_id),
  status               TEXT NOT NULL,
  score_delta          INT DEFAULT 0,
  public_message       TEXT,
  private_message      TEXT,
  evidence_json        JSONB,
  started_at           TIMESTAMP,
  completed_at         TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scoring_events (
  scoring_event_id TEXT PRIMARY KEY,
  event_id         TEXT NOT NULL REFERENCES events(event_id),
  team_id          TEXT,
  user_id          TEXT,
  quest_id         TEXT,
  task_id          TEXT,
  source_type      TEXT NOT NULL,
  source_id        TEXT NOT NULL,
  points_delta     INT NOT NULL,
  reason           TEXT NOT NULL,
  created_by       TEXT,
  created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  idempotency_key  TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS hints_taken (
  event_id       TEXT NOT NULL REFERENCES events(event_id),
  team_id        TEXT NOT NULL REFERENCES teams(team_id),
  task_id        TEXT NOT NULL REFERENCES quest_tasks(task_id),
  hint_id        TEXT NOT NULL REFERENCES task_hints(hint_id),
  taken_by       TEXT NOT NULL,
  penalty_points INT DEFAULT 0,
  taken_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (event_id, team_id, hint_id)
);

-- ── Host operations & audit ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS announcements (
  announcement_id TEXT PRIMARY KEY,
  event_id        TEXT NOT NULL REFERENCES events(event_id),
  title           TEXT NOT NULL,
  body_md         TEXT NOT NULL,
  severity        TEXT DEFAULT 'info',
  created_by      TEXT NOT NULL,
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manual_adjustments (
  adjustment_id TEXT PRIMARY KEY,
  event_id      TEXT NOT NULL REFERENCES events(event_id),
  team_id       TEXT,
  user_id       TEXT,
  task_id       TEXT,
  points_delta  INT NOT NULL,
  reason        TEXT NOT NULL,
  created_by    TEXT NOT NULL,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_audit_log (
  audit_id      TEXT PRIMARY KEY,
  event_id      TEXT,
  actor_user_id TEXT,
  action        TEXT NOT NULL,
  target_type   TEXT,
  target_id     TEXT,
  payload_json  JSONB,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Helpful indexes ──────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_quests_pack_version    ON quests(pack_version_id);
CREATE INDEX IF NOT EXISTS idx_quest_tasks_quest       ON quest_tasks(quest_id);
CREATE INDEX IF NOT EXISTS idx_task_validators_task    ON task_validators(task_id);
CREATE INDEX IF NOT EXISTS idx_task_hints_task         ON task_hints(task_id);
CREATE INDEX IF NOT EXISTS idx_events_status           ON events(status);
CREATE INDEX IF NOT EXISTS idx_teams_event             ON teams(event_id);
CREATE INDEX IF NOT EXISTS idx_participants_event      ON participants(event_id);
CREATE INDEX IF NOT EXISTS idx_attempts_event_team     ON task_attempts(event_id, team_id);
CREATE INDEX IF NOT EXISTS idx_attempts_task           ON task_attempts(task_id);
CREATE INDEX IF NOT EXISTS idx_validation_attempt      ON validation_results(attempt_id);
CREATE INDEX IF NOT EXISTS idx_scoring_event_team      ON scoring_events(event_id, team_id);

-- ── Derived views ────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW team_scores AS
SELECT
  event_id,
  team_id,
  SUM(points_delta) AS total_points,
  COUNT(*)          AS scoring_events,
  MAX(created_at)   AS last_scored_at
FROM scoring_events
GROUP BY event_id, team_id;

CREATE OR REPLACE VIEW event_leaderboard AS
SELECT
  s.event_id,
  s.team_id,
  t.display_name,
  s.total_points,
  RANK() OVER (PARTITION BY s.event_id ORDER BY s.total_points DESC, s.last_scored_at ASC) AS rank,
  s.last_scored_at
FROM team_scores s
JOIN teams t ON s.team_id = t.team_id;

CREATE OR REPLACE VIEW task_completion_status AS
SELECT
  a.event_id,
  a.team_id,
  a.task_id,
  MAX(CASE WHEN a.status = 'passed' THEN 1 ELSE 0 END) AS completed,
  COUNT(*)            AS attempts,
  MAX(a.completed_at) AS last_attempt_at
FROM task_attempts a
GROUP BY a.event_id, a.team_id, a.task_id;
