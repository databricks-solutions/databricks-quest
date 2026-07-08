"""Repository for event announcements (host → players broadcast).

Announcements are a simple per-event feed the host composes from the console
and players see in the lobby. Reads are tolerant of an unavailable Lakebase
(return empty); the create path raises so the host gets a clear error.
"""

import logging
import uuid
from typing import Any, Dict, List

import db

logger = logging.getLogger("databricks-quest.repositories.announcements")

VALID_SEVERITIES = ("info", "warning", "critical")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class AnnouncementsRepository:
    """Access to the ``announcements`` table."""

    def list_for_event(self, event_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT announcement_id, event_id, title, body_md, severity,
                       created_by, created_at
                FROM announcements
                WHERE event_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (event_id, limit),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_for_event failed: %s", exc)
            return []

    def create(
        self,
        *,
        event_id: str,
        title: str,
        body_md: str,
        created_by: str,
        severity: str = "info",
    ) -> Dict[str, Any]:
        """Insert an announcement and return the new row."""
        sev = (severity or "info").lower()
        if sev not in VALID_SEVERITIES:
            sev = "info"
        announcement_id = _new_id("ann")
        db.execute(
            """
            INSERT INTO announcements
                (announcement_id, event_id, title, body_md, severity, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (announcement_id, event_id, title, body_md, sev, created_by),
        )
        return {
            "announcement_id": announcement_id,
            "event_id": event_id,
            "title": title,
            "body_md": body_md,
            "severity": sev,
            "created_by": created_by,
        }
