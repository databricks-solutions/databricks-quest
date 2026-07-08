-- description: Multi-workspace GameDay federation (ADR_006) — shared-Lakebase schema
--
-- Applied ONCE to the shared (master) Lakebase. Children connect to the
-- already-migrated DB and never run migrations themselves. In a standalone
-- deploy this same migration runs against the local Lakebase and its new
-- columns/tables simply sit unused — one schema, used differently.
--
-- Backward compatible: every new column is nullable, and the identity-resolving
-- leaderboard view preserves the standalone team_id path via COALESCE, so
-- adoption/standalone behaviour is unchanged. Every statement is idempotent.

-- ── Nullable workspace_id on the event-fact tables ───────────────────────────
-- Federated rows carry workspace_id (+ user_id = labuser); standalone rows
-- leave it NULL. ADD COLUMN IF NOT EXISTS is idempotent on re-run.

ALTER TABLE scoring_events     ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE task_attempts      ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE validation_results ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE hints_taken        ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE participants       ADD COLUMN IF NOT EXISTS workspace_id TEXT;

-- ── Child workspace registry (populated by the child's startup check-in) ─────

CREATE TABLE IF NOT EXISTS event_workspaces (
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

-- ── Central identity map: (event, workspace, labuser) → real person / team ───
-- The roster import populates this; the leaderboard view resolves federated
-- scoring_events through it. ``source`` records provenance:
--   roster      — imported from a host roster CSV
--   self_claim  — player self-identified (future)
--   pending     — placeholder row awaiting reconciliation (future)

CREATE TABLE IF NOT EXISTS participant_identity_map (
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

CREATE INDEX IF NOT EXISTS idx_pim_event_team   ON participant_identity_map(event_id, team_id);
CREATE INDEX IF NOT EXISTS idx_pim_resolve      ON participant_identity_map(event_id, workspace_id, lab_user_email);
CREATE INDEX IF NOT EXISTS idx_scoring_ws       ON scoring_events(event_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_event_workspaces_event ON event_workspaces(event_id);

-- ── Identity-resolving team_scores ───────────────────────────────────────────
-- Federated scoring_events carry (workspace_id, user_id=labuser) but no
-- team_id; resolve their team via the identity map. Standalone rows already
-- carry team_id (and NULL workspace_id, so the join finds nothing) and fall
-- back to it through COALESCE. Rows that resolve to no team (unmapped) are
-- excluded here and surfaced separately for host reconciliation.
-- CREATE OR REPLACE keeps the same column list/order as migration 001 so the
-- dependent event_leaderboard view does not need to be dropped.

CREATE OR REPLACE VIEW team_scores AS
SELECT
  se.event_id,
  COALESCE(se.team_id, pim.team_id) AS team_id,
  SUM(se.points_delta) AS total_points,
  COUNT(*)             AS scoring_events,
  MAX(se.created_at)   AS last_scored_at
FROM scoring_events se
LEFT JOIN participant_identity_map pim
       ON pim.event_id       = se.event_id
      AND pim.workspace_id   = se.workspace_id
      AND pim.lab_user_email = se.user_id
WHERE COALESCE(se.team_id, pim.team_id) IS NOT NULL
GROUP BY se.event_id, COALESCE(se.team_id, pim.team_id);

-- event_leaderboard is unchanged in shape; it now transitively spans every
-- workspace because team_scores does. Re-declared for clarity/idempotency.

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

-- ── Unmapped federated identities (for host reconciliation) ──────────────────
-- Federated scoring_events whose (workspace_id, user_id) is not yet resolvable
-- to a team. Nothing is lost — these attribute the moment the roster is
-- (re-)imported. Excludes standalone rows (workspace_id IS NULL).

CREATE OR REPLACE VIEW unmapped_identities AS
SELECT
  se.event_id,
  se.workspace_id,
  se.user_id            AS lab_user_email,
  COUNT(*)              AS scoring_events,
  SUM(se.points_delta)  AS unattributed_points,
  MAX(se.created_at)    AS last_seen_at
FROM scoring_events se
LEFT JOIN participant_identity_map pim
       ON pim.event_id       = se.event_id
      AND pim.workspace_id   = se.workspace_id
      AND pim.lab_user_email = se.user_id
WHERE se.workspace_id IS NOT NULL
  AND se.team_id IS NULL
  AND pim.team_id IS NULL
GROUP BY se.event_id, se.workspace_id, se.user_id;
