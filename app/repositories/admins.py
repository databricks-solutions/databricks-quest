"""Repository for the DB-backed admin allowlist (``quest_admins``).

The admin set lives in Lakebase so it is one shared source of truth. In
federation the master owns the table and child apps read/write it through the
shared event-writer role, so an admin is automatically an admin across the
standalone GameDay app, the master, and every child workspace.

The deploy-time env allowlist (``QUEST_ADMIN_ALLOWLIST``) seeds this table on
startup and is always honoured as a fallback by ``main.is_admin_user`` so the
deploying user keeps access even when the table is empty or unreachable. All
read/write paths degrade gracefully (raise to the caller, which wraps them).
"""

import logging
from typing import Any, Dict, List

import db

logger = logging.getLogger("databricks-quest.repositories.admins")

# Mirrors migration 004_admins.sql so a standalone app whose Event Mode
# migrations were skipped can still create the table on startup.
ENSURE_DDL = """
CREATE TABLE IF NOT EXISTS quest_admins (
  email     TEXT PRIMARY KEY,
  added_by  TEXT,
  source    TEXT NOT NULL DEFAULT 'manual',
  added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _norm(email: str) -> str:
    return (email or "").strip().lower()


class AdminsRepository:
    """Access to the shared ``quest_admins`` allowlist."""

    def ensure_schema(self) -> bool:
        """Best-effort create. Returns False when the role lacks DDL (child)."""
        try:
            db.execute(ENSURE_DDL)
            return True
        except Exception as exc:  # noqa: BLE001 - child role has no CREATE; ignore
            logger.info("admins ensure_schema skipped: %s", exc)
            return False

    def list_emails(self) -> List[str]:
        """Lowercased admin emails. Raises if the table is missing/unreadable."""
        rows = db.execute_query("SELECT email FROM quest_admins")
        return [_norm(r["email"]) for r in rows if r.get("email")]

    def list_admins(self) -> List[Dict[str, Any]]:
        return db.execute_query(
            "SELECT email, added_by, source, added_at "
            "FROM quest_admins ORDER BY added_at ASC, email ASC"
        )

    def add(self, email: str, added_by: str, source: str = "manual") -> bool:
        """Insert an admin. Returns True when a new row was created."""
        e = _norm(email)
        if not e:
            return False
        n = db.execute(
            "INSERT INTO quest_admins (email, added_by, source) VALUES (%s, %s, %s) "
            "ON CONFLICT (email) DO NOTHING",
            (e, added_by, source),
        )
        return n > 0

    def remove(self, email: str) -> int:
        return db.execute("DELETE FROM quest_admins WHERE email = %s", (_norm(email),))

    def seed(self, emails: List[str], added_by: str = "deploy:seed") -> int:
        """Insert env-allowlist emails as 'seed' rows. Best-effort, idempotent."""
        added = 0
        for email in emails:
            try:
                if self.add(email, added_by, source="seed"):
                    added += 1
            except Exception as exc:  # noqa: BLE001 - one bad row shouldn't abort
                logger.info("admin seed skip %s: %s", email, exc)
        return added
