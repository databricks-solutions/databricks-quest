"""Repository for event-scoped scoring and leaderboards.

Reads from the append-only ``scoring_events`` table and the derived
``team_scores`` / ``event_leaderboard`` views created by migration 001.
"""

import logging
from typing import Any, Dict, List, Optional

import db

logger = logging.getLogger("databricks-quest.repositories.leaderboard")


class LeaderboardRepository:
    """Access to scoring events and the event leaderboard views."""

    def get_team_leaderboard(self, event_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT event_id, team_id, display_name, total_points, rank,
                       last_scored_at
                FROM event_leaderboard
                WHERE event_id = %s
                ORDER BY rank ASC
                """,
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_team_leaderboard failed: %s", exc)
            return []

    def get_team_score(self, event_id: str, team_id: str) -> int:
        try:
            rows = db.execute_query(
                """
                SELECT COALESCE(total_points, 0) AS total_points
                FROM team_scores
                WHERE event_id = %s AND team_id = %s
                """,
                (event_id, team_id),
            )
            return int(rows[0]["total_points"]) if rows else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_team_score failed: %s", exc)
            return 0

    def completed_task_ids(self, event_id: str, team_id: str) -> List[str]:
        """Task ids a team has been awarded points for in an event.

        A task counts as complete once it has a positive base-points scoring
        event for the team (the per-scope idempotency key guarantees once-only).
        """
        if not team_id:
            return []
        try:
            rows = db.execute_query(
                """
                SELECT DISTINCT task_id
                FROM scoring_events
                WHERE event_id = %s AND team_id = %s
                  AND task_id IS NOT NULL AND points_delta > 0
                """,
                (event_id, team_id),
            )
            return [r["task_id"] for r in rows if r.get("task_id")]
        except Exception as exc:  # noqa: BLE001
            logger.warning("completed_task_ids failed: %s", exc)
            return []

    def get_team_rank(self, event_id: str, team_id: str) -> Optional[int]:
        """A team's rank in the event leaderboard, or None if unranked."""
        if not team_id:
            return None
        try:
            rows = db.execute_query(
                "SELECT rank FROM event_leaderboard WHERE event_id = %s AND team_id = %s",
                (event_id, team_id),
            )
            return int(rows[0]["rank"]) if rows and rows[0].get("rank") is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_team_rank failed: %s", exc)
            return None

    def list_recent_scoring_events(
        self, event_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT scoring_event_id, team_id, user_id, task_id,
                       source_type, points_delta, reason, created_at
                FROM scoring_events
                WHERE event_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (event_id, limit),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_recent_scoring_events failed: %s", exc)
            return []

    def recent_scoring_feed(
        self, event_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Recent scoring events enriched with team + task names for display.

        Powers the live "activity feed" next to the leaderboard. Uses LEFT JOINs
        so a row with a null ``team_id`` (federated, pre-reconciliation) or a
        manual adjustment with no task still renders.
        """
        try:
            return db.execute_query(
                """
                SELECT se.scoring_event_id, se.team_id, t.display_name AS team_name,
                       se.task_id, qt.title AS task_title, se.source_type,
                       se.points_delta, se.reason, se.created_at
                FROM scoring_events se
                LEFT JOIN teams t        ON se.team_id = t.team_id
                LEFT JOIN quest_tasks qt ON se.task_id = qt.task_id
                WHERE se.event_id = %s
                ORDER BY se.created_at DESC
                LIMIT %s
                """,
                (event_id, limit),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("recent_scoring_feed failed: %s", exc)
            return []

    def revealed_hint_ids(self, event_id: str, team_id: str) -> List[str]:
        """Hint ids a team has already revealed (and been charged for).

        Hint penalties are recorded as ``scoring_events`` with
        ``source_type = 'hint_penalty'`` and ``source_id`` set to the hint id.
        The unique idempotency key guarantees a team is charged at most once per
        hint, so this set lets the runner show an already-revealed hint without
        re-charging.
        """
        if not team_id:
            return []
        try:
            rows = db.execute_query(
                """
                SELECT DISTINCT source_id
                FROM scoring_events
                WHERE event_id = %s AND team_id = %s
                  AND source_type = 'hint_penalty'
                """,
                (event_id, team_id),
            )
            return [r["source_id"] for r in rows if r.get("source_id")]
        except Exception as exc:  # noqa: BLE001
            logger.warning("revealed_hint_ids failed: %s", exc)
            return []

    # ── Mutations (deferred to later PRs) ────────────────────────────────────

    def record_scoring_event(self, *args, **kwargs):
        raise NotImplementedError(
            "record_scoring_event is implemented in a later PR"
        )
