-- description: Allow federated attempts without a team_id at write time (PR03)
--
-- A federation child records a task_attempt stamped with workspace_id and the
-- lab user, but the team is only known after the host imports the roster. The
-- scoring ledger (scoring_events.team_id) was already nullable for exactly this
-- reason; task_attempts must match so the child write path works.
--
-- Standalone/master attempts continue to carry a team_id. Backward compatible
-- and idempotent: dropping NOT NULL on an already-nullable column is a no-op.

ALTER TABLE task_attempts ALTER COLUMN team_id DROP NOT NULL;

-- Validation results for federated attempts attribute back to a workspace too;
-- index it so per-workspace validation health (host console) stays cheap.
CREATE INDEX IF NOT EXISTS idx_validation_results_ws
  ON validation_results(workspace_id);
