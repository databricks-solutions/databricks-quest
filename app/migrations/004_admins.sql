-- description: DB-backed admin allowlist (quest_admins) — shared across apps
--
-- Moves the admin allowlist from a static deploy-time env var into Lakebase so
-- it is a single shared source of truth. In federation the master owns this
-- table and child apps read/write it through the shared event-writer role, so
-- an admin is automatically an admin across the standalone GameDay app, the
-- master, and every child workspace.
--
-- The deploy-time env allowlist (QUEST_ADMIN_ALLOWLIST) seeds this table on
-- startup and is always honoured as a fallback, so the deploying user keeps
-- access even before any row exists. Idempotent and additive: re-running is a
-- no-op and adoption-mode tables are untouched.

CREATE TABLE IF NOT EXISTS quest_admins (
  email     TEXT PRIMARY KEY,
  added_by  TEXT,
  source    TEXT NOT NULL DEFAULT 'manual',
  added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
