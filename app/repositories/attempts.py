"""Repository for task attempts and their validation results.

Reads cover attempt/validation history; writes (PR03) persist a submitted
attempt, record one normalized row per validator result, and transition the
attempt's terminal status. Federated rows carry ``workspace_id`` (and a NULL
``team_id`` resolved later via the identity map); standalone rows carry
``team_id`` and leave ``workspace_id`` NULL.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

import db

logger = logging.getLogger("databricks-quest.repositories.attempts")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class AttemptsRepository:
    """Access to the ``task_attempts`` / ``validation_results`` tables."""

    def get_attempt(self, attempt_id: str) -> Optional[Dict[str, Any]]:
        try:
            rows = db.execute_query(
                "SELECT * FROM task_attempts WHERE attempt_id = %s", (attempt_id,)
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_attempt failed: %s", exc)
            return None

    def list_attempts_for_team(
        self, event_id: str, team_id: str
    ) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT attempt_id, task_id, submitted_by, status,
                       submitted_at, completed_at, error_message
                FROM task_attempts
                WHERE event_id = %s AND team_id = %s
                ORDER BY submitted_at DESC
                """,
                (event_id, team_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_attempts_for_team failed: %s", exc)
            return []

    def list_validation_results(self, attempt_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT validation_result_id, validator_id, status, score_delta,
                       public_message, started_at, completed_at
                FROM validation_results
                WHERE attempt_id = %s
                ORDER BY completed_at ASC NULLS LAST
                """,
                (attempt_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_validation_results failed: %s", exc)
            return []

    def list_event_attempts(
        self, event_id: str, status: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Host view: recent attempts across an event, newest first.

        Joins task title and team name so the console can render a readable
        queue/results/failed view. Optional ``status`` filters to one terminal
        state (e.g. ``failed``, ``passed``, ``manual``, ``error``, ``running``).
        """
        params: List[Any] = [event_id]
        status_clause = ""
        if status:
            status_clause = "AND a.status = %s"
            params.append(status)
        params.append(limit)
        try:
            return db.execute_query(
                f"""
                SELECT a.attempt_id, a.task_id, a.team_id, a.submitted_by,
                       a.status, a.submitted_at, a.completed_at, a.error_message,
                       t.title AS task_title, tm.display_name AS team_name
                FROM task_attempts a
                LEFT JOIN quest_tasks t ON t.task_id = a.task_id
                LEFT JOIN teams tm ON tm.team_id = a.team_id
                WHERE a.event_id = %s {status_clause}
                ORDER BY a.submitted_at DESC
                LIMIT %s
                """,
                tuple(params),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_event_attempts failed: %s", exc)
            return []

    def attempt_status_counts(self, event_id: str) -> Dict[str, int]:
        """Counts of attempts per status for an event (host dashboard tiles)."""
        try:
            rows = db.execute_query(
                """
                SELECT status, COUNT(*) AS c
                FROM task_attempts
                WHERE event_id = %s
                GROUP BY status
                """,
                (event_id,),
            )
            return {r["status"]: int(r["c"]) for r in rows}
        except Exception as exc:  # noqa: BLE001
            logger.warning("attempt_status_counts failed: %s", exc)
            return {}

    def list_validation_results_admin(self, attempt_id: str) -> List[Dict[str, Any]]:
        """Host view of validator results — includes the private diagnostics."""
        try:
            return db.execute_query(
                """
                SELECT validation_result_id, validator_id, status, score_delta,
                       public_message, private_message, started_at, completed_at
                FROM validation_results
                WHERE attempt_id = %s
                ORDER BY completed_at ASC NULLS LAST
                """,
                (attempt_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_validation_results_admin failed: %s", exc)
            return []

    # ── Mutations (PR03) ─────────────────────────────────────────────────────

    def create_attempt(
        self,
        *,
        event_id: str,
        task_id: str,
        submitted_by: str,
        submission: Optional[Dict[str, Any]] = None,
        team_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        status: str = "running",
    ) -> str:
        """Insert a new attempt row and return its id.

        Raises on failure so the request handler can surface a host-safe error
        (the attempt is the anchor for everything that follows).
        """
        from psycopg2.extras import Json

        attempt_id = _new_id("att")
        db.execute(
            """
            INSERT INTO task_attempts
                (attempt_id, event_id, team_id, task_id, submitted_by,
                 submission_json, status, started_at, workspace_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
            """,
            (
                attempt_id,
                event_id,
                team_id,
                task_id,
                submitted_by,
                Json(submission) if submission is not None else None,
                status,
                workspace_id,
            ),
        )
        return attempt_id

    def record_validation_result(
        self,
        *,
        attempt_id: str,
        validator_id: str,
        status: str,
        score_delta: int = 0,
        public_message: Optional[str] = None,
        private_message: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        workspace_id: Optional[str] = None,
    ) -> str:
        """Persist one validator's normalized outcome; return its row id."""
        from psycopg2.extras import Json

        result_id = _new_id("vres")
        db.execute(
            """
            INSERT INTO validation_results
                (validation_result_id, attempt_id, validator_id, status,
                 score_delta, public_message, private_message, evidence_json,
                 started_at, completed_at, workspace_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, %s)
            """,
            (
                result_id,
                attempt_id,
                validator_id,
                status,
                score_delta,
                public_message,
                private_message,
                Json(evidence) if evidence is not None else None,
                workspace_id,
            ),
        )
        return result_id

    def set_status(
        self,
        attempt_id: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Transition an attempt to a terminal status and stamp completion."""
        db.execute(
            """
            UPDATE task_attempts
            SET status = %s,
                error_message = %s,
                completed_at = CURRENT_TIMESTAMP
            WHERE attempt_id = %s
            """,
            (status, error_message, attempt_id),
        )
