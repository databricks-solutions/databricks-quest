"""Repository for the append-only scoring ledger (``scoring_events``).

The ledger is the single source of truth for points; leaderboards/profiles are
derived views over it. Writes are idempotent on ``idempotency_key`` (a UNIQUE
column), so a retried submission or a racing federated writer can never
double-award. Federated rows additionally carry ``workspace_id`` and leave
``team_id`` NULL (resolved later via the identity map).
"""

import logging
import uuid
from typing import Any, Dict, Optional

import db

logger = logging.getLogger("databricks-quest.repositories.scoring")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ScoringRepository:
    """Access to ``scoring_events`` and derived point totals."""

    def insert_scoring_event(
        self,
        *,
        event_id: str,
        idempotency_key: str,
        points_delta: int,
        source_type: str,
        source_id: str,
        reason: str,
        task_id: Optional[str] = None,
        quest_id: Optional[str] = None,
        team_id: Optional[str] = None,
        user_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert a scoring event idempotently.

        Returns ``{"awarded": bool, "points": int, "scoring_event_id": str|None}``.
        ``awarded`` is False when the idempotency key already existed (no
        double-award), in which case no row was written.
        """
        scoring_event_id = _new_id("score")
        try:
            rowcount = db.execute(
                """
                INSERT INTO scoring_events
                    (scoring_event_id, event_id, team_id, user_id, quest_id,
                     task_id, source_type, source_id, points_delta, reason,
                     created_by, workspace_id, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (
                    scoring_event_id,
                    event_id,
                    team_id,
                    user_id,
                    quest_id,
                    task_id,
                    source_type,
                    source_id,
                    points_delta,
                    reason,
                    created_by,
                    workspace_id,
                    idempotency_key,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("insert_scoring_event failed: %s", exc)
            raise

        if rowcount and rowcount > 0:
            return {
                "awarded": True,
                "points": points_delta,
                "scoring_event_id": scoring_event_id,
            }
        # Key already present — points were awarded by a prior submission.
        return {"awarded": False, "points": 0, "scoring_event_id": None}

    def record_manual_adjustment(
        self,
        *,
        event_id: str,
        points_delta: int,
        reason: str,
        created_by: str,
        team_id: Optional[str] = None,
        user_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a host manual score adjustment.

        Writes a ``manual_adjustments`` audit row and a matching
        ``scoring_events`` ledger row in one transaction so the leaderboard
        reflects the change immediately. The ledger row carries a unique
        idempotency key (the adjustment id) so it always lands — unlike base
        points, a manual override is intentional and never deduplicated.
        Returns ``{"adjustment_id", "scoring_event_id", "points_delta"}``.
        """
        adjustment_id = _new_id("adj")
        scoring_event_id = _new_id("score")
        try:
            with db.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO manual_adjustments
                        (adjustment_id, event_id, team_id, user_id, task_id,
                         points_delta, reason, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (adjustment_id, event_id, team_id, user_id, task_id,
                     points_delta, reason, created_by),
                )
                cur.execute(
                    """
                    INSERT INTO scoring_events
                        (scoring_event_id, event_id, team_id, user_id, quest_id,
                         task_id, source_type, source_id, points_delta, reason,
                         created_by, workspace_id, idempotency_key)
                    VALUES (%s, %s, %s, %s, NULL, %s, 'manual_adjustment', %s, %s, %s, %s, NULL, %s)
                    """,
                    (scoring_event_id, event_id, team_id, user_id, task_id,
                     adjustment_id, points_delta, reason, created_by, adjustment_id),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("record_manual_adjustment failed: %s", exc)
            raise
        return {
            "adjustment_id": adjustment_id,
            "scoring_event_id": scoring_event_id,
            "points_delta": points_delta,
        }

    def get_team_total(self, event_id: str, team_id: str) -> int:
        try:
            rows = db.execute_query(
                "SELECT COALESCE(SUM(points_delta), 0) AS pts "
                "FROM scoring_events WHERE event_id = %s AND team_id = %s",
                (event_id, team_id),
            )
            return int(rows[0]["pts"]) if rows else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_team_total failed: %s", exc)
            return 0
