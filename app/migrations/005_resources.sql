-- description: Event resource registry (event_resources) for bootstrap/reset health
--
-- Tracks the per-team Databricks resources (catalogs/schemas) an event has
-- provisioned so the host console can show resource health and so reset only
-- ever touches resources this event created. A row is upserted on bootstrap and
-- flipped to 'removed' on reset; the FQN is the catalog.schema the team's
-- validators template against (${team_catalog}.${team_schema}).
--
-- This is operational state only — the authoritative safety guard is the
-- namespace check in services/namespace.py, which refuses any target outside
-- the event's computed namespace regardless of what is recorded here.
-- Idempotent and additive; adoption-mode tables are untouched.

CREATE TABLE IF NOT EXISTS event_resources (
  resource_id    TEXT PRIMARY KEY,
  event_id       TEXT NOT NULL REFERENCES events(event_id),
  team_id        TEXT REFERENCES teams(team_id),
  resource_type  TEXT NOT NULL,            -- 'catalog' | 'schema'
  fqn            TEXT NOT NULL,            -- fully-qualified name (catalog or catalog.schema)
  status         TEXT NOT NULL DEFAULT 'pending', -- pending | active | failed | removed
  message        TEXT,
  created_by     TEXT,
  created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (event_id, fqn)
);

CREATE INDEX IF NOT EXISTS idx_event_resources_event ON event_resources(event_id);
CREATE INDEX IF NOT EXISTS idx_event_resources_team  ON event_resources(team_id);
