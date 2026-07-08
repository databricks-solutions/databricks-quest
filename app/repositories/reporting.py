"""Read-only aggregation queries for the post-event report (PR11).

Everything here is SELECT-only and degrades to an empty result if Lakebase is
unavailable, so report generation never raises. The shaping/scoring of these
rows into a report (and the follow-up heuristics) lives in
``services/report_service.py`` so it can be unit-tested without a database.
"""

import logging
from typing import Any, Dict, List

import db

logger = logging.getLogger("databricks-quest.repositories.reporting")


class ReportingRepository:
    def task_catalog(self, pack_version_id: str) -> List[Dict[str, Any]]:
        """Every quest/task in the event's pack, in play order."""
        try:
            return db.execute_query(
                """
                SELECT q.slug AS quest_slug, q.title AS quest_title,
                       q.sort_order AS quest_sort,
                       t.task_id, t.slug AS task_slug, t.title AS task_title,
                       t.points, t.sort_order AS task_sort
                FROM quests q
                JOIN quest_tasks t ON t.quest_id = q.quest_id
                WHERE q.pack_version_id = %s
                ORDER BY q.sort_order ASC, t.sort_order ASC
                """,
                (pack_version_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("task_catalog failed: %s", exc)
            return []

    def team_task_completion(self, event_id: str) -> List[Dict[str, Any]]:
        """(team_id, task_id) pairs a team has completed (positive base points)."""
        try:
            return db.execute_query(
                """
                SELECT DISTINCT team_id, task_id
                FROM scoring_events
                WHERE event_id = %s AND source_type = 'validation'
                  AND points_delta > 0 AND task_id IS NOT NULL AND team_id IS NOT NULL
                """,
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("team_task_completion failed: %s", exc)
            return []

    def failure_summary(self, event_id: str) -> List[Dict[str, Any]]:
        """Failed/error attempt counts per task (the validation-failures view)."""
        try:
            return db.execute_query(
                """
                SELECT a.task_id, t.title AS task_title, a.status,
                       COUNT(*) AS attempts
                FROM task_attempts a
                LEFT JOIN quest_tasks t ON t.task_id = a.task_id
                WHERE a.event_id = %s AND a.status IN ('failed', 'error')
                GROUP BY a.task_id, t.title, a.status
                ORDER BY attempts DESC
                """,
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failure_summary failed: %s", exc)
            return []

    def hint_usage(self, event_id: str) -> List[Dict[str, Any]]:
        """Hint reveals (penalties) enriched with team + task names."""
        try:
            return db.execute_query(
                """
                SELECT se.team_id, tm.display_name AS team_name,
                       se.source_id AS hint_id, se.task_id, qt.title AS task_title,
                       se.points_delta, se.created_at
                FROM scoring_events se
                LEFT JOIN teams tm ON tm.team_id = se.team_id
                LEFT JOIN quest_tasks qt ON qt.task_id = se.task_id
                WHERE se.event_id = %s AND se.source_type = 'hint_penalty'
                ORDER BY se.created_at ASC
                """,
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("hint_usage failed: %s", exc)
            return []

    def participant_roster(self, event_id: str) -> List[Dict[str, Any]]:
        """Every participant with their team and personally-submitted attempt stats.

        Points are scored at the team level, so per-person attribution here is the
        honest individual signal: how many distinct tasks the participant
        personally submitted that passed, and their total attempts. Left joins keep
        un-teamed participants (and those who never submitted) in the roster.
        """
        try:
            return db.execute_query(
                """
                SELECT p.participant_id, p.user_id, p.display_name, p.role,
                       t.team_id, t.display_name AS team_name, t.name AS team_slug,
                       COUNT(a.attempt_id) AS attempts_total,
                       COUNT(DISTINCT CASE WHEN a.status = 'passed' THEN a.task_id END)
                           AS tasks_passed
                FROM participants p
                LEFT JOIN team_members tmem ON tmem.participant_id = p.participant_id
                LEFT JOIN teams t ON t.team_id = tmem.team_id AND t.event_id = p.event_id
                LEFT JOIN task_attempts a
                       ON a.submitted_by = p.user_id AND a.event_id = p.event_id
                WHERE p.event_id = %s
                GROUP BY p.participant_id, p.user_id, p.display_name, p.role,
                         t.team_id, t.display_name, t.name
                ORDER BY t.display_name ASC NULLS LAST, p.display_name ASC
                """,
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("participant_roster failed: %s", exc)
            return []

    def first_solves(self, event_id: str) -> List[Dict[str, Any]]:
        """The first team to complete each task (champions / first-blood)."""
        try:
            return db.execute_query(
                """
                SELECT task_id, team_id, created_at FROM (
                    SELECT se.task_id, se.team_id, se.created_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY se.task_id ORDER BY se.created_at ASC
                           ) AS rn
                    FROM scoring_events se
                    WHERE se.event_id = %s AND se.source_type = 'validation'
                      AND se.points_delta > 0 AND se.task_id IS NOT NULL
                      AND se.team_id IS NOT NULL
                ) ranked
                WHERE rn = 1
                """,
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("first_solves failed: %s", exc)
            return []
