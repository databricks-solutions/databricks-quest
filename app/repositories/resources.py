"""Repository for the event resource registry (``event_resources``).

Operational state only: which catalogs/schemas an event provisioned and their
health. The authoritative safety guard lives in ``services/namespace.py``.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

import db

logger = logging.getLogger("databricks-quest.repositories.resources")


def _new_id() -> str:
    return f"res_{uuid.uuid4().hex[:12]}"


class ResourcesRepository:
    """Access to ``event_resources``."""

    def upsert(
        self,
        *,
        event_id: str,
        fqn: str,
        resource_type: str,
        status: str,
        team_id: Optional[str] = None,
        message: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> None:
        """Insert or update a resource row keyed on (event_id, fqn)."""
        try:
            db.execute(
                """
                INSERT INTO event_resources
                    (resource_id, event_id, team_id, resource_type, fqn, status, message, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id, fqn) DO UPDATE SET
                    status = EXCLUDED.status,
                    message = EXCLUDED.message,
                    team_id = COALESCE(EXCLUDED.team_id, event_resources.team_id),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (_new_id(), event_id, team_id, resource_type, fqn, status, message, created_by),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("event_resources upsert failed for %s: %s", fqn, exc)

    def list_for_event(self, event_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT resource_id, event_id, team_id, resource_type, fqn,
                       status, message, created_by, created_at, updated_at
                FROM event_resources
                WHERE event_id = %s
                ORDER BY fqn ASC
                """,
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("event_resources list failed: %s", exc)
            return []
