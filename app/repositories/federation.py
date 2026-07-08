"""Repository for multi-workspace GameDay federation (ADR_006).

Owns the federation tables introduced by migration 002:

- ``event_workspaces``         — child workspace registry (startup check-in)
- ``participant_identity_map`` — central (event, workspace, labuser) → person/team

All reads degrade gracefully (return empty/None) when Lakebase is unavailable,
mirroring the rest of the data layer. Writes used by the master (roster import)
run inside a transaction so a partial import never lands.
"""

import csv
import io
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

import db

logger = logging.getLogger("databricks-quest.repositories.federation")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# Accepted CSV header aliases → canonical column. Workspace can be given as an
# id or a host; lab user / display / email / team have a few natural spellings.
_HEADER_ALIASES = {
    "workspace_id": "workspace_id",
    "workspace": "workspace_id",
    "workspace_host": "workspace_host",
    "host": "workspace_host",
    "lab_user_email": "lab_user_email",
    "lab_user": "lab_user_email",
    "labuser": "lab_user_email",
    "email": "lab_user_email",
    "display_name": "display_name",
    "name": "display_name",
    "real_email": "real_email",
    "real_name_email": "real_email",
    "team_name": "team_name",
    "team": "team_name",
}


class RosterImportError(Exception):
    """Raised when a roster CSV cannot be parsed into usable rows."""


class FederationRepository:
    """Access to ``event_workspaces`` and ``participant_identity_map``."""

    # ── Child startup check-in ───────────────────────────────────────────────

    def checkin_workspace(
        self,
        workspace_id: str,
        event_id: Optional[str] = None,
        event_slug: Optional[str] = None,
        workspace_host: Optional[str] = None,
        app_url: Optional[str] = None,
        app_version: Optional[str] = None,
    ) -> bool:
        """Upsert a child's presence row. Best-effort; never raises."""
        if not workspace_id:
            return False
        try:
            db.execute(
                """
                INSERT INTO event_workspaces
                    (workspace_id, event_id, event_slug, workspace_host,
                     app_url, app_version, status, registered_at, last_seen_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'active',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (workspace_id) DO UPDATE SET
                    event_id       = COALESCE(EXCLUDED.event_id, event_workspaces.event_id),
                    event_slug     = COALESCE(EXCLUDED.event_slug, event_workspaces.event_slug),
                    workspace_host = COALESCE(EXCLUDED.workspace_host, event_workspaces.workspace_host),
                    app_url        = COALESCE(EXCLUDED.app_url, event_workspaces.app_url),
                    app_version    = COALESCE(EXCLUDED.app_version, event_workspaces.app_version),
                    status         = 'active',
                    last_seen_at   = CURRENT_TIMESTAMP
                """,
                (workspace_id, event_id, event_slug, workspace_host, app_url, app_version),
            )
            return True
        except Exception as exc:  # noqa: BLE001 - check-in must never break boot
            logger.warning("checkin_workspace failed: %s", exc)
            return False

    def list_event_workspaces(self, event_id: str) -> List[Dict[str, Any]]:
        """Per-workspace health: presence joined with live write/validation counts."""
        try:
            return db.execute_query(
                """
                SELECT
                    w.workspace_id,
                    w.event_slug,
                    w.workspace_host,
                    w.app_url,
                    w.app_version,
                    w.status,
                    w.registered_at,
                    w.last_seen_at,
                    COALESCE(sc.scoring_events, 0)   AS scoring_events,
                    COALESCE(sc.points, 0)           AS points,
                    COALESCE(vc.validations, 0)      AS validations,
                    COALESCE(vc.passes, 0)           AS validation_passes
                FROM event_workspaces w
                LEFT JOIN (
                    SELECT workspace_id,
                           COUNT(*)           AS scoring_events,
                           SUM(points_delta)  AS points
                    FROM scoring_events
                    WHERE event_id = %s AND workspace_id IS NOT NULL
                    GROUP BY workspace_id
                ) sc ON sc.workspace_id = w.workspace_id
                LEFT JOIN (
                    SELECT workspace_id,
                           COUNT(*) AS validations,
                           SUM(CASE WHEN status = 'passed' THEN 1 ELSE 0 END) AS passes
                    FROM validation_results
                    WHERE workspace_id IS NOT NULL
                    GROUP BY workspace_id
                ) vc ON vc.workspace_id = w.workspace_id
                WHERE w.event_id = %s OR w.event_id IS NULL
                ORDER BY w.last_seen_at DESC NULLS LAST
                """,
                (event_id, event_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_event_workspaces failed: %s", exc)
            return []

    # ── Identity resolution ──────────────────────────────────────────────────

    def resolve_identity(
        self, event_id: str, workspace_id: str, lab_user_email: str
    ) -> Optional[Dict[str, Any]]:
        """Return the mapped team/participant for a (workspace, labuser), or None."""
        try:
            rows = db.execute_query(
                """
                SELECT pim.team_id, pim.participant_id, pim.display_name,
                       pim.real_email, pim.source, t.display_name AS team_display_name,
                       t.name AS team_name
                FROM participant_identity_map pim
                LEFT JOIN teams t ON t.team_id = pim.team_id
                WHERE pim.event_id = %s AND pim.workspace_id = %s
                  AND pim.lab_user_email = %s
                """,
                (event_id, workspace_id, lab_user_email),
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("resolve_identity failed: %s", exc)
            return None

    def list_unmapped_identities(self, event_id: str) -> List[Dict[str, Any]]:
        """Federated scoring rows not yet attributable to a team."""
        try:
            return db.execute_query(
                """
                SELECT event_id, workspace_id, lab_user_email,
                       scoring_events, unattributed_points, last_seen_at
                FROM unmapped_identities
                WHERE event_id = %s
                ORDER BY unattributed_points DESC NULLS LAST
                """,
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_unmapped_identities failed: %s", exc)
            return []

    # ── Roster import (master) ───────────────────────────────────────────────

    @staticmethod
    def parse_roster_csv(csv_text: str) -> List[Dict[str, str]]:
        """Parse a roster CSV into normalized rows.

        Required per row: a workspace reference (``workspace_id`` or
        ``workspace_host``), ``lab_user_email``, and ``team_name``. Extra
        columns are ignored. Raises :class:`RosterImportError` on unusable input.
        """
        text = (csv_text or "").strip()
        if not text:
            raise RosterImportError("Roster CSV is empty.")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise RosterImportError("Roster CSV has no header row.")

        # Map provided headers → canonical names.
        colmap: Dict[str, str] = {}
        for raw in reader.fieldnames:
            key = (raw or "").strip().lower().replace(" ", "_")
            if key in _HEADER_ALIASES:
                colmap[raw] = _HEADER_ALIASES[key]

        canon = set(colmap.values())
        if "lab_user_email" not in canon:
            raise RosterImportError("Roster CSV must include a 'lab_user_email' (or 'email') column.")
        if "team_name" not in canon:
            raise RosterImportError("Roster CSV must include a 'team_name' (or 'team') column.")
        if "workspace_id" not in canon and "workspace_host" not in canon:
            raise RosterImportError(
                "Roster CSV must include a 'workspace_id' or 'workspace_host' column."
            )

        rows: List[Dict[str, str]] = []
        for i, raw_row in enumerate(reader, start=2):  # row 1 is the header
            norm: Dict[str, str] = {}
            for raw_key, canon_key in colmap.items():
                val = (raw_row.get(raw_key) or "").strip()
                if val:
                    norm[canon_key] = val
            # workspace_id falls back to workspace_host when only the host is given.
            ws = norm.get("workspace_id") or norm.get("workspace_host")
            lab = norm.get("lab_user_email")
            team = norm.get("team_name")
            if not (ws and lab and team):
                # Skip blank/partial lines rather than failing the whole import.
                if any(norm.values()):
                    logger.warning("roster row %d skipped (missing workspace/labuser/team)", i)
                continue
            norm["workspace_id"] = ws
            rows.append(norm)

        if not rows:
            raise RosterImportError("Roster CSV had a valid header but no usable data rows.")
        return rows

    def import_roster(self, event_id: str, csv_text: str) -> Dict[str, Any]:
        """Idempotently import a roster: create teams/participants + identity map.

        Re-import is safe: teams are matched by (event_id, name), participants by
        (event_id, user_id=real_email or lab_user_email), and identity-map rows
        are upserted on their natural key. Returns a summary with counts.
        """
        rows = self.parse_roster_csv(csv_text)

        teams_seen: Dict[str, str] = {}      # team_name -> team_id
        participants_created = 0
        teams_created = 0
        mappings = 0

        with db.transaction() as cur:
            for r in rows:
                team_name = r["team_name"]
                lab_user = r["lab_user_email"]
                workspace_id = r["workspace_id"]
                display_name = r.get("display_name") or lab_user.split("@")[0]
                real_email = r.get("real_email")

                # ── Team (idempotent on event_id + name) ─────────────────────
                team_id = teams_seen.get(team_name)
                if not team_id:
                    cur.execute(
                        "SELECT team_id FROM teams WHERE event_id = %s AND name = %s",
                        (event_id, team_name),
                    )
                    found = cur.fetchone()
                    if found:
                        team_id = found[0]
                    else:
                        team_id = _new_id("team")
                        cur.execute(
                            """
                            INSERT INTO teams (team_id, event_id, name, display_name)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (event_id, name) DO NOTHING
                            """,
                            (team_id, event_id, team_name, team_name),
                        )
                        # Re-read in case a concurrent/ON CONFLICT path won.
                        cur.execute(
                            "SELECT team_id FROM teams WHERE event_id = %s AND name = %s",
                            (event_id, team_name),
                        )
                        team_id = cur.fetchone()[0]
                        teams_created += 1
                    teams_seen[team_name] = team_id

                # ── Participant (idempotent on event_id + user_id) ───────────
                user_id = real_email or lab_user
                cur.execute(
                    "SELECT participant_id FROM participants WHERE event_id = %s AND user_id = %s",
                    (event_id, user_id),
                )
                found = cur.fetchone()
                if found:
                    participant_id = found[0]
                    cur.execute(
                        """
                        UPDATE participants
                        SET display_name = COALESCE(%s, display_name),
                            email = COALESCE(%s, email),
                            workspace_id = COALESCE(%s, workspace_id)
                        WHERE participant_id = %s
                        """,
                        (display_name, real_email, workspace_id, participant_id),
                    )
                else:
                    participant_id = _new_id("part")
                    cur.execute(
                        """
                        INSERT INTO participants
                            (participant_id, event_id, user_id, display_name,
                             email, role, workspace_id)
                        VALUES (%s, %s, %s, %s, %s, 'player', %s)
                        ON CONFLICT (event_id, user_id) DO NOTHING
                        """,
                        (participant_id, event_id, user_id, display_name, real_email, workspace_id),
                    )
                    cur.execute(
                        "SELECT participant_id FROM participants WHERE event_id = %s AND user_id = %s",
                        (event_id, user_id),
                    )
                    participant_id = cur.fetchone()[0]
                    participants_created += 1

                # ── team_members (idempotent) ────────────────────────────────
                cur.execute(
                    """
                    INSERT INTO team_members (team_id, participant_id)
                    VALUES (%s, %s)
                    ON CONFLICT (team_id, participant_id) DO NOTHING
                    """,
                    (team_id, participant_id),
                )

                # ── Identity map (upsert on natural key) ─────────────────────
                cur.execute(
                    """
                    INSERT INTO participant_identity_map
                        (event_id, workspace_id, lab_user_email, participant_id,
                         team_id, real_email, display_name, source, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'roster', CURRENT_TIMESTAMP)
                    ON CONFLICT (event_id, workspace_id, lab_user_email) DO UPDATE SET
                        participant_id = EXCLUDED.participant_id,
                        team_id        = EXCLUDED.team_id,
                        real_email     = EXCLUDED.real_email,
                        display_name   = EXCLUDED.display_name,
                        source         = 'roster',
                        updated_at     = CURRENT_TIMESTAMP
                    """,
                    (event_id, workspace_id, lab_user, participant_id, team_id, real_email, display_name),
                )
                mappings += 1

        return {
            "event_id": event_id,
            "rows": len(rows),
            "teams_created": teams_created,
            "participants_created": participants_created,
            "identities_mapped": mappings,
            "status": "imported",
        }
