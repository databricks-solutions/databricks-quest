"""Repository for events, hosts, teams, and participants.

Read helpers back the lobby/event APIs; the mutation paths (create event,
manage teams, register participants, lifecycle transitions) are implemented in
PR04 and write an audit trail via the calling endpoints.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

import db

logger = logging.getLogger("databricks-quest.repositories.events")

# ── Event lifecycle state machine (PR04) ─────────────────────────────────────
# A small, explicit state machine keeps the host console honest and gives
# players a clear signal about when play is open. Attempts are only accepted
# while ``active``; everything else blocks submission with a safe message.

VALID_STATUSES = (
    "draft",
    "ready",
    "active",
    "paused",
    "frozen",
    "completed",
    "archived",
)

ALLOWED_TRANSITIONS: Dict[str, set] = {
    "draft": {"ready", "active", "archived"},
    "ready": {"active", "archived"},
    "active": {"paused", "frozen", "completed"},
    "paused": {"active", "frozen", "completed"},
    "frozen": {"active", "completed"},
    "completed": {"archived"},
    "archived": set(),
}

# Only an active event accepts new attempts / awards points.
ATTEMPT_OPEN_STATUSES = {"active"}
# Statuses a player may self-join.
JOINABLE_STATUSES = {"ready", "active", "paused"}
# Statuses surfaced to players in the lobby list.
PLAYER_VISIBLE_STATUSES = {"ready", "active", "paused", "frozen"}


class EventStateError(Exception):
    """Raised for invalid lifecycle transitions or conflicting mutations.

    Carries an HTTP ``status`` and a short ``code`` so endpoints can translate
    it into a consistent error envelope without re-deriving intent.
    """

    def __init__(self, message: str, code: str = "CONFLICT", status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def can_transition(current: str, target: str) -> bool:
    """Return True if ``current`` → ``target`` is an allowed lifecycle move."""
    return target in ALLOWED_TRANSITIONS.get(current, set())


def attempts_open(status: str) -> bool:
    """Return True if an event in ``status`` accepts new attempts."""
    return status in ATTEMPT_OPEN_STATUSES


class EventsRepository:
    """Access to the ``events`` / ``teams`` / ``participants`` tables."""

    def list_active_events(self) -> List[Dict[str, Any]]:
        """Return events that are visible to players (active/paused/frozen).

        Returns an empty list if Lakebase or the GameDay tables are unavailable.
        """
        try:
            return db.execute_query(
                """
                SELECT e.event_id, e.slug, e.title, e.status,
                       e.starts_at, e.ends_at, e.timezone,
                       p.title AS pack_title
                FROM events e
                LEFT JOIN quest_pack_versions pv
                       ON e.pack_version_id = pv.pack_version_id
                LEFT JOIN quest_packs p ON pv.pack_id = p.pack_id
                WHERE e.status IN ('active', 'paused', 'frozen')
                ORDER BY e.starts_at ASC NULLS LAST
                """
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_active_events failed: %s", exc)
            return []

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Return a single event row, or None if absent/unavailable."""
        try:
            rows = db.execute_query(
                "SELECT * FROM events WHERE event_id = %s", (event_id,)
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_event failed: %s", exc)
            return None

    def get_event_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Return a single event row by slug, or None if absent/unavailable."""
        try:
            rows = db.execute_query(
                "SELECT * FROM events WHERE slug = %s", (slug,)
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_event_by_slug failed: %s", exc)
            return None

    def list_event_hosts(self, event_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                "SELECT user_id, role FROM event_hosts WHERE event_id = %s ORDER BY user_id ASC",
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_event_hosts failed: %s", exc)
            return []

    def add_host(self, event_id: str, user_id: str, role: str = "host") -> bool:
        """Grant a user the host role for an event (idempotent).

        Returns True when a new row was inserted, False if already a host.
        """
        if role not in ("owner", "host"):
            role = "host"
        try:
            affected = db.execute(
                """
                INSERT INTO event_hosts (event_id, user_id, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (event_id, user_id) DO NOTHING
                """,
                (event_id, user_id, role),
            )
            return bool(affected)
        except Exception as exc:  # noqa: BLE001
            logger.warning("add_host failed: %s", exc)
            raise EventStateError("Could not add host.", "HOST_WRITE_FAILED", 503)

    def remove_host(self, event_id: str, user_id: str) -> None:
        """Revoke a user's host role. Refuses to remove the last owner.

        Raises :class:`EventStateError` when the target is the only ``owner`` row
        so an event can never be left without an owning host.
        """
        hosts = self.list_event_hosts(event_id)
        target = next((h for h in hosts if (h.get("user_id") or "").lower() == (user_id or "").lower()), None)
        if not target:
            raise EventStateError("Not a host of this event.", "NOT_A_HOST", 404)
        owners = [h for h in hosts if (h.get("role") or "") == "owner"]
        if target.get("role") == "owner" and len(owners) <= 1:
            raise EventStateError(
                "Cannot remove the last owner of an event.", "LAST_OWNER", 409
            )
        try:
            db.execute(
                "DELETE FROM event_hosts WHERE event_id = %s AND user_id = %s",
                (event_id, target.get("user_id")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("remove_host failed: %s", exc)
            raise EventStateError("Could not remove host.", "HOST_WRITE_FAILED", 503)

    def list_teams(self, event_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                "SELECT * FROM teams WHERE event_id = %s ORDER BY name ASC",
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_teams failed: %s", exc)
            return []

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        try:
            rows = db.execute_query(
                "SELECT * FROM teams WHERE team_id = %s", (team_id,)
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_team failed: %s", exc)
            return None

    def get_team_for_user(
        self, event_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Resolve the team a user belongs to in an event (standalone path).

        Returns the full team row (including ``team_catalog``/``team_schema`` used
        as validator template variables), or None if the user is not on a team.
        """
        try:
            rows = db.execute_query(
                """
                SELECT t.*
                FROM teams t
                JOIN team_members tm ON tm.team_id = t.team_id
                JOIN participants p ON p.participant_id = tm.participant_id
                WHERE p.event_id = %s AND p.user_id = %s
                ORDER BY tm.joined_at ASC
                LIMIT 1
                """,
                (event_id, user_id),
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_team_for_user failed: %s", exc)
            return None

    def list_team_members(self, team_id: str) -> List[Dict[str, Any]]:
        """Members of a team (participant + display name), join order."""
        try:
            return db.execute_query(
                """
                SELECT p.participant_id, p.user_id, p.display_name, p.role,
                       tm.joined_at
                FROM team_members tm
                JOIN participants p ON p.participant_id = tm.participant_id
                WHERE tm.team_id = %s
                ORDER BY tm.joined_at ASC
                """,
                (team_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_team_members failed: %s", exc)
            return []

    def get_participant(self, event_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            rows = db.execute_query(
                "SELECT * FROM participants WHERE event_id = %s AND user_id = %s",
                (event_id, user_id),
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_participant failed: %s", exc)
            return None

    # ── Lobby read helpers (PR04) ────────────────────────────────────────────

    def list_player_events(self) -> List[Dict[str, Any]]:
        """Events visible to players (ready/active/paused/frozen) with team counts."""
        try:
            return db.execute_query(
                """
                SELECT e.event_id, e.slug, e.title, e.description, e.status,
                       e.starts_at, e.ends_at, e.timezone,
                       p.title AS pack_title,
                       (SELECT COUNT(*) FROM teams t WHERE t.event_id = e.event_id) AS team_count
                FROM events e
                LEFT JOIN quest_pack_versions pv ON e.pack_version_id = pv.pack_version_id
                LEFT JOIN quest_packs p ON pv.pack_id = p.pack_id
                WHERE e.status IN ('ready', 'active', 'paused', 'frozen')
                ORDER BY e.starts_at ASC NULLS LAST, e.title ASC
                """
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_player_events failed: %s", exc)
            return []

    def list_teams_with_counts(self, event_id: str) -> List[Dict[str, Any]]:
        """Teams in an event with their member counts (lobby/host view)."""
        try:
            return db.execute_query(
                """
                SELECT t.team_id, t.name, t.display_name, t.color,
                       t.team_catalog, t.team_schema, t.created_at,
                       COALESCE(m.members, 0) AS members
                FROM teams t
                LEFT JOIN (
                    SELECT team_id, COUNT(*) AS members
                    FROM team_members GROUP BY team_id
                ) m ON m.team_id = t.team_id
                WHERE t.event_id = %s
                ORDER BY t.name ASC
                """,
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_teams_with_counts failed: %s", exc)
            return []

    def event_counts(self, event_id: str, pack_version_id: Optional[str]) -> Dict[str, int]:
        """Participant/team/quest/task counts for an event lobby header."""
        counts = {"participants": 0, "teams": 0, "quests": 0, "tasks": 0}
        try:
            rows = db.execute_query(
                "SELECT COUNT(*) AS c FROM participants WHERE event_id = %s", (event_id,)
            )
            counts["participants"] = rows[0]["c"] if rows else 0
            rows = db.execute_query(
                "SELECT COUNT(*) AS c FROM teams WHERE event_id = %s", (event_id,)
            )
            counts["teams"] = rows[0]["c"] if rows else 0
            if pack_version_id:
                rows = db.execute_query(
                    "SELECT COUNT(*) AS c FROM quests WHERE pack_version_id = %s",
                    (pack_version_id,),
                )
                counts["quests"] = rows[0]["c"] if rows else 0
                rows = db.execute_query(
                    """
                    SELECT COUNT(*) AS c FROM quest_tasks qt
                    JOIN quests q ON q.quest_id = qt.quest_id
                    WHERE q.pack_version_id = %s
                    """,
                    (pack_version_id,),
                )
                counts["tasks"] = rows[0]["c"] if rows else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("event_counts failed: %s", exc)
        return counts

    def is_host(self, event_id: str, user_id: str) -> bool:
        try:
            rows = db.execute_query(
                "SELECT 1 FROM event_hosts WHERE event_id = %s AND user_id = %s",
                (event_id, user_id),
            )
            return bool(rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("is_host failed: %s", exc)
            return False

    # ── Mutations (PR04) ─────────────────────────────────────────────────────

    def create_event(
        self,
        *,
        slug: str,
        title: str,
        pack_version_id: str,
        created_by: str,
        description: Optional[str] = None,
        mode: str = "gameday",
        timezone: str = "UTC",
        status: str = "draft",
    ) -> Dict[str, Any]:
        """Create an event from an imported pack version; register the creator as host.

        Raises :class:`EventStateError` on a duplicate slug or unknown pack
        version. Event + host row are written atomically.
        """
        if status not in VALID_STATUSES:
            raise EventStateError(f"Invalid status '{status}'.", "INVALID_STATUS", 400)

        pv = db.execute_query(
            "SELECT pack_version_id FROM quest_pack_versions WHERE pack_version_id = %s",
            (pack_version_id,),
        )
        if not pv:
            raise EventStateError(
                "Quest pack version not found — import a pack first.",
                "PACK_VERSION_NOT_FOUND",
                400,
            )

        if self.get_event_by_slug(slug):
            raise EventStateError(f"An event with slug '{slug}' already exists.", "SLUG_EXISTS", 409)

        event_id = _new_id("evt")
        try:
            with db.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO events
                        (event_id, slug, title, description, pack_version_id,
                         mode, status, timezone, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (event_id, slug, title, description, pack_version_id,
                     mode, status, timezone, created_by),
                )
                cur.execute(
                    """
                    INSERT INTO event_hosts (event_id, user_id, role)
                    VALUES (%s, %s, 'owner')
                    ON CONFLICT (event_id, user_id) DO NOTHING
                    """,
                    (event_id, created_by),
                )
        except EventStateError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("create_event failed: %s", exc)
            raise EventStateError("Could not create event.", "CREATE_FAILED", 503)

        return self.get_event(event_id)

    def set_status(self, event_id: str, target: str, actor: str) -> Dict[str, Any]:
        """Transition an event to ``target`` if the move is allowed.

        Stamps lifecycle timestamps (first ``active`` → starts_at, ``completed``
        → ends_at, ``frozen`` → scoring_frozen_at, and clears the freeze on
        unfreeze). Raises :class:`EventStateError` on unknown/invalid moves.
        """
        if target not in VALID_STATUSES:
            raise EventStateError(f"Unknown status '{target}'.", "INVALID_STATUS", 400)
        ev = self.get_event(event_id)
        if not ev:
            raise EventStateError("Event not found.", "NOT_FOUND", 404)
        current = ev["status"]
        if current == target:
            return ev
        if not can_transition(current, target):
            raise EventStateError(
                f"Cannot move event from '{current}' to '{target}'.",
                "INVALID_TRANSITION",
                409,
            )

        sets = ["status = %s", "updated_at = CURRENT_TIMESTAMP"]
        params: List[Any] = [target]
        if target == "active" and not ev.get("starts_at"):
            sets.append("starts_at = CURRENT_TIMESTAMP")
        if target == "completed" and not ev.get("ends_at"):
            sets.append("ends_at = CURRENT_TIMESTAMP")
        if target == "frozen":
            sets.append("scoring_frozen_at = CURRENT_TIMESTAMP")
        if current == "frozen" and target == "active":
            sets.append("scoring_frozen_at = NULL")
        params.append(event_id)

        try:
            db.execute(
                f"UPDATE events SET {', '.join(sets)} WHERE event_id = %s", tuple(params)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("set_status failed: %s", exc)
            raise EventStateError("Could not update event status.", "UPDATE_FAILED", 503)
        return self.get_event(event_id)

    def create_team(
        self,
        *,
        event_id: str,
        name: str,
        display_name: Optional[str] = None,
        color: Optional[str] = None,
        team_catalog: Optional[str] = None,
        team_schema: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a team in an event. Raises on duplicate (event_id, name)."""
        if not name or not name.strip():
            raise EventStateError("Team name is required.", "INVALID_TEAM", 400)
        name = name.strip()
        existing = db.execute_query(
            "SELECT team_id FROM teams WHERE event_id = %s AND name = %s",
            (event_id, name),
        )
        if existing:
            raise EventStateError(f"Team '{name}' already exists in this event.", "TEAM_EXISTS", 409)
        team_id = _new_id("team")
        try:
            db.execute(
                """
                INSERT INTO teams
                    (team_id, event_id, name, display_name, color, team_catalog, team_schema)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (team_id, event_id, name, display_name or name, color, team_catalog, team_schema),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("create_team failed: %s", exc)
            raise EventStateError("Could not create team.", "CREATE_FAILED", 503)
        return self.get_team(team_id)

    def rename_team(self, team_id: str, display_name: str) -> Dict[str, Any]:
        """Update a team's display name (player self-service rename).

        Only the human-facing ``display_name`` changes; the immutable ``name``
        (used for namespace derivation) is left untouched so resource targets
        stay stable.
        """
        display_name = (display_name or "").strip()
        if not display_name:
            raise EventStateError("Display name is required.", "INVALID_TEAM", 400)
        try:
            updated = db.execute(
                "UPDATE teams SET display_name = %s WHERE team_id = %s",
                (display_name, team_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("rename_team failed: %s", exc)
            raise EventStateError("Could not rename team.", "RENAME_FAILED", 503)
        if not updated:
            raise EventStateError("Team not found.", "TEAM_NOT_FOUND", 404)
        return self.get_team(team_id)

    def register_participant(
        self,
        *,
        event_id: str,
        user_id: str,
        display_name: Optional[str] = None,
        email: Optional[str] = None,
        role: str = "player",
    ) -> Dict[str, Any]:
        """Idempotently register a participant (keyed on event_id + user_id)."""
        existing = self.get_participant(event_id, user_id)
        if existing:
            return existing
        participant_id = _new_id("part")
        try:
            db.execute(
                """
                INSERT INTO participants
                    (participant_id, event_id, user_id, display_name, email, role)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id, user_id) DO NOTHING
                """,
                (participant_id, event_id, user_id, display_name or (email or user_id).split("@")[0], email, role),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("register_participant failed: %s", exc)
            raise EventStateError("Could not register participant.", "REGISTER_FAILED", 503)
        return self.get_participant(event_id, user_id)

    def set_team_namespace(
        self, team_id: str, catalog: str, schema: str
    ) -> None:
        """Persist a team's resolved catalog/schema (called after bootstrap).

        Stamps the values the resource bootstrap actually provisioned so the
        validator-variable resolution and the host resource view show a stable,
        materialized namespace rather than re-deriving it each request.
        """
        try:
            db.execute(
                "UPDATE teams SET team_catalog = %s, team_schema = %s WHERE team_id = %s",
                (catalog, schema, team_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("set_team_namespace failed: %s", exc)

    def set_participant_team(
        self, event_id: str, participant_id: str, team_id: str
    ) -> None:
        """Place a participant on exactly one team within an event (reassign).

        Enforces the single-team-per-event invariant that scoring relies on:
        any prior membership on another team *in this event* is removed before
        the target membership is added. Idempotent and atomic.
        """
        try:
            with db.transaction() as cur:
                cur.execute(
                    """
                    DELETE FROM team_members
                    WHERE participant_id = %s
                      AND team_id IN (SELECT team_id FROM teams WHERE event_id = %s)
                      AND team_id <> %s
                    """,
                    (participant_id, event_id, team_id),
                )
                cur.execute(
                    """
                    INSERT INTO team_members (team_id, participant_id)
                    VALUES (%s, %s)
                    ON CONFLICT (team_id, participant_id) DO NOTHING
                    """,
                    (team_id, participant_id),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("set_participant_team failed: %s", exc)
            raise EventStateError("Could not assign participant to team.", "ASSIGN_FAILED", 503)

    def import_participants(
        self, event_id: str, rows: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """Bulk-register participants with optional team assignment.

        Each row: ``{user_id|email (required), display_name?, team_name?}``.
        Teams named in rows are created on demand (idempotent). Returns counts.
        Runs in one transaction so a partial import never lands.
        """
        clean: List[Dict[str, str]] = []
        for r in rows:
            uid = (r.get("user_id") or r.get("email") or "").strip()
            if not uid:
                continue
            clean.append({
                "user_id": uid,
                "email": (r.get("email") or uid).strip(),
                "display_name": (r.get("display_name") or "").strip() or uid.split("@")[0],
                "team_name": (r.get("team_name") or "").strip(),
            })
        if not clean:
            raise EventStateError("No usable participant rows (need user_id or email).", "INVALID_IMPORT", 400)

        teams_seen: Dict[str, str] = {}
        teams_created = participants_created = assignments = 0
        try:
            with db.transaction() as cur:
                for r in clean:
                    team_id = None
                    if r["team_name"]:
                        team_id = teams_seen.get(r["team_name"])
                        if not team_id:
                            cur.execute(
                                "SELECT team_id FROM teams WHERE event_id = %s AND name = %s",
                                (event_id, r["team_name"]),
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
                                    (team_id, event_id, r["team_name"], r["team_name"]),
                                )
                                cur.execute(
                                    "SELECT team_id FROM teams WHERE event_id = %s AND name = %s",
                                    (event_id, r["team_name"]),
                                )
                                team_id = cur.fetchone()[0]
                                teams_created += 1
                            teams_seen[r["team_name"]] = team_id

                    cur.execute(
                        "SELECT participant_id FROM participants WHERE event_id = %s AND user_id = %s",
                        (event_id, r["user_id"]),
                    )
                    found = cur.fetchone()
                    if found:
                        participant_id = found[0]
                    else:
                        participant_id = _new_id("part")
                        cur.execute(
                            """
                            INSERT INTO participants
                                (participant_id, event_id, user_id, display_name, email, role)
                            VALUES (%s, %s, %s, %s, %s, 'player')
                            ON CONFLICT (event_id, user_id) DO NOTHING
                            """,
                            (participant_id, event_id, r["user_id"], r["display_name"], r["email"]),
                        )
                        cur.execute(
                            "SELECT participant_id FROM participants WHERE event_id = %s AND user_id = %s",
                            (event_id, r["user_id"]),
                        )
                        participant_id = cur.fetchone()[0]
                        participants_created += 1

                    if team_id:
                        # Single-team-per-event invariant: drop any prior
                        # membership on a different team in THIS event before
                        # adding the target, so a re-import to a new team can
                        # never leave a participant on two teams (which would
                        # double-count scoring).
                        cur.execute(
                            """
                            DELETE FROM team_members
                            WHERE participant_id = %s
                              AND team_id IN (SELECT team_id FROM teams WHERE event_id = %s)
                              AND team_id <> %s
                            """,
                            (participant_id, event_id, team_id),
                        )
                        cur.execute(
                            """
                            INSERT INTO team_members (team_id, participant_id)
                            VALUES (%s, %s)
                            ON CONFLICT (team_id, participant_id) DO NOTHING
                            """,
                            (team_id, participant_id),
                        )
                        assignments += 1
        except EventStateError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("import_participants failed: %s", exc)
            raise EventStateError("Could not import participants.", "IMPORT_FAILED", 503)

        return {
            "rows": len(clean),
            "participants_created": participants_created,
            "teams_created": teams_created,
            "assignments": assignments,
        }
