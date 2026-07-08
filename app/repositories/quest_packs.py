"""Repository for quest packs, versions, quests, tasks, validators, and hints.

PR01 scope: read helpers plus stubs for import/lint, which depend on the quest
pack schema parser and validator allowlist introduced in later PRs.
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import db
from models.quest_pack import QuestPackManifest

logger = logging.getLogger("databricks-quest.repositories.quest_packs")


class ImmutableVersionError(Exception):
    """Raised when re-importing an existing pack version with different content."""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class QuestPacksRepository:
    """Access to the quest pack catalog tables."""

    def list_packs(self) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                "SELECT * FROM quest_packs ORDER BY updated_at DESC"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_packs failed: %s", exc)
            return []

    def get_pack(self, pack_id: str) -> Optional[Dict[str, Any]]:
        try:
            rows = db.execute_query(
                "SELECT * FROM quest_packs WHERE pack_id = %s", (pack_id,)
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_pack failed: %s", exc)
            return None

    def list_versions(self, pack_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT pack_version_id, pack_id, version, content_hash,
                       status, imported_by, imported_at
                FROM quest_pack_versions
                WHERE pack_id = %s
                ORDER BY imported_at DESC
                """,
                (pack_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_versions failed: %s", exc)
            return []

    def list_quests(self, pack_version_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT quest_id, slug, title, category, difficulty,
                       sort_order, base_points
                FROM quests
                WHERE pack_version_id = %s
                ORDER BY sort_order ASC, title ASC
                """,
                (pack_version_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_quests failed: %s", exc)
            return []

    def get_quest(self, quest_id: str) -> Optional[Dict[str, Any]]:
        """Return a single quest row (including narrative), or None."""
        try:
            rows = db.execute_query(
                """
                SELECT quest_id, pack_version_id, slug, title, narrative,
                       category, difficulty, sort_order, base_points
                FROM quests
                WHERE quest_id = %s
                """,
                (quest_id,),
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_quest failed: %s", exc)
            return None

    def list_tasks_detail(self, quest_id: str) -> List[Dict[str, Any]]:
        """Tasks for a quest with player-facing instructions/criteria."""
        try:
            return db.execute_query(
                """
                SELECT task_id, slug, title, objective, instructions_md,
                       success_criteria_md, points, sort_order, validation_mode
                FROM quest_tasks
                WHERE quest_id = %s
                ORDER BY sort_order ASC, title ASC
                """,
                (quest_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_tasks_detail failed: %s", exc)
            return []

    def list_hints(self, task_id: str) -> List[Dict[str, Any]]:
        """Hints for a task in reveal order (with any point penalty)."""
        try:
            return db.execute_query(
                """
                SELECT hint_id, task_id, sort_order, title, body_md, penalty_points
                FROM task_hints
                WHERE task_id = %s
                ORDER BY sort_order ASC
                """,
                (task_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_hints failed: %s", exc)
            return []

    def get_hint(self, hint_id: str) -> Optional[Dict[str, Any]]:
        """Return a single hint row joined to its task/quest (for reveal scoring)."""
        try:
            rows = db.execute_query(
                """
                SELECT h.hint_id, h.task_id, h.title, h.body_md, h.penalty_points,
                       h.sort_order, t.quest_id
                FROM task_hints h
                JOIN quest_tasks t ON t.task_id = h.task_id
                WHERE h.hint_id = %s
                """,
                (hint_id,),
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_hint failed: %s", exc)
            return None

    def list_tasks(self, quest_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT task_id, slug, title, objective, points,
                       sort_order, validation_mode
                FROM quest_tasks
                WHERE quest_id = %s
                ORDER BY sort_order ASC, title ASC
                """,
                (quest_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_tasks failed: %s", exc)
            return []

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Return a single task row joined to its quest (for the attempt path)."""
        try:
            rows = db.execute_query(
                """
                SELECT t.task_id, t.quest_id, t.slug, t.title, t.objective,
                       t.points, t.validation_mode, q.pack_version_id
                FROM quest_tasks t
                JOIN quests q ON q.quest_id = t.quest_id
                WHERE t.task_id = %s
                """,
                (task_id,),
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_task failed: %s", exc)
            return None

    def list_validators(self, task_id: str) -> List[Dict[str, Any]]:
        """Return enabled validators for a task, in authoring order."""
        try:
            return db.execute_query(
                """
                SELECT validator_id, task_id, type, mode, config_json,
                       expected_json, timeout_seconds, sort_order, enabled
                FROM task_validators
                WHERE task_id = %s AND enabled = TRUE
                ORDER BY sort_order ASC
                """,
                (task_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_validators failed: %s", exc)
            return []

    def find_pack_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        try:
            rows = db.execute_query(
                "SELECT * FROM quest_packs WHERE slug = %s", (slug,)
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("find_pack_by_slug failed: %s", exc)
            return None

    def get_version(self, pack_version_id: str) -> Optional[Dict[str, Any]]:
        """Return a pack version row including its stored ``manifest_json``.

        Used by the resource bootstrap service to read the pack's optional
        ``resources`` section (seed SQL) for an event's pack version.
        """
        try:
            rows = db.execute_query(
                "SELECT * FROM quest_pack_versions WHERE pack_version_id = %s",
                (pack_version_id,),
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_version failed: %s", exc)
            return None

    def find_version(self, pack_id: str, version: str) -> Optional[Dict[str, Any]]:
        try:
            rows = db.execute_query(
                "SELECT * FROM quest_pack_versions WHERE pack_id = %s AND version = %s",
                (pack_id, version),
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("find_version failed: %s", exc)
            return None

    def get_pack_detail(self, pack_id: str) -> Optional[Dict[str, Any]]:
        """Return a pack with its versions and per-version content counts."""
        pack = self.get_pack(pack_id)
        if not pack:
            return None
        versions = self.list_versions(pack_id)
        for v in versions:
            pvid = v["pack_version_id"]
            try:
                qc = db.execute_query(
                    "SELECT COUNT(*) AS c FROM quests WHERE pack_version_id = %s",
                    (pvid,),
                )
                tc = db.execute_query(
                    """
                    SELECT COUNT(*) AS c
                    FROM quest_tasks t
                    JOIN quests q ON t.quest_id = q.quest_id
                    WHERE q.pack_version_id = %s
                    """,
                    (pvid,),
                )
                v["quest_count"] = int(qc[0]["c"]) if qc else 0
                v["task_count"] = int(tc[0]["c"]) if tc else 0
            except Exception as exc:  # noqa: BLE001
                logger.warning("get_pack_detail counts failed: %s", exc)
        pack["versions"] = versions
        return pack

    # ── Import (PR02) ────────────────────────────────────────────────────────

    def import_manifest(
        self,
        manifest: QuestPackManifest,
        content_hash: str,
        actor: Optional[str],
    ) -> Dict[str, Any]:
        """Persist a manifest as a new immutable pack version.

        Idempotent on (slug, version, content_hash): re-importing identical
        content returns the existing version with status ``duplicate``.
        Re-importing the same version with *different* content raises
        :class:`ImmutableVersionError` (versions are immutable).
        """
        from psycopg2.extras import Json

        slug = manifest.pack.slug
        version = manifest.pack.version
        counts = manifest.counts()

        existing_pack = self.find_pack_by_slug(slug)
        pack_id = existing_pack["pack_id"] if existing_pack else None

        if pack_id:
            existing_ver = self.find_version(pack_id, version)
            if existing_ver:
                if existing_ver.get("content_hash") == content_hash:
                    return {
                        "pack_id": pack_id,
                        "pack_version_id": existing_ver["pack_version_id"],
                        "status": "duplicate",
                        "counts": counts,
                    }
                raise ImmutableVersionError(
                    f"Pack '{slug}' version '{version}' already exists with "
                    "different content. Bump pack.version to import changes."
                )

        manifest_dump = manifest.model_dump(mode="json", exclude_none=True)
        pack_version_id = _new_id("packver")

        with db.transaction() as cur:
            if not pack_id:
                pack_id = _new_id("pack")
                cur.execute(
                    """
                    INSERT INTO quest_packs
                        (pack_id, slug, title, description, owner, status, created_by)
                    VALUES (%s, %s, %s, %s, %s, 'draft', %s)
                    """,
                    (
                        pack_id,
                        slug,
                        manifest.pack.title,
                        manifest.pack.description,
                        manifest.pack.owner,
                        actor,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE quest_packs
                    SET title = %s,
                        description = %s,
                        owner = COALESCE(%s, owner),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE pack_id = %s
                    """,
                    (
                        manifest.pack.title,
                        manifest.pack.description,
                        manifest.pack.owner,
                        pack_id,
                    ),
                )

            cur.execute(
                """
                INSERT INTO quest_pack_versions
                    (pack_version_id, pack_id, version, manifest_json,
                     content_hash, status, imported_by)
                VALUES (%s, %s, %s, %s, %s, 'imported', %s)
                """,
                (
                    pack_version_id,
                    pack_id,
                    version,
                    Json(manifest_dump),
                    content_hash,
                    actor,
                ),
            )

            for qi, quest in enumerate(manifest.quests):
                quest_id = _new_id("qst")
                cur.execute(
                    """
                    INSERT INTO quests
                        (quest_id, pack_version_id, slug, title, narrative,
                         category, difficulty, sort_order, base_points,
                         unlock_rule_json, facilitator_notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        quest_id,
                        pack_version_id,
                        quest.slug,
                        quest.title,
                        quest.narrative_md,
                        quest.category,
                        quest.difficulty,
                        quest.sort_order if quest.sort_order is not None else qi,
                        quest.base_points,
                        Json(quest.unlock_rule.model_dump(exclude_none=True))
                        if quest.unlock_rule
                        else None,
                        quest.facilitator_notes,
                    ),
                )

                for ti, task in enumerate(quest.tasks):
                    task_id = _new_id("tsk")
                    cur.execute(
                        """
                        INSERT INTO quest_tasks
                            (task_id, quest_id, slug, title, objective,
                             instructions_md, success_criteria_md, points,
                             sort_order, validation_mode, scoring_json, metadata_json)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            task_id,
                            quest_id,
                            task.slug,
                            task.title,
                            task.objective,
                            task.instructions_md,
                            task.success_criteria_md,
                            task.points,
                            task.sort_order if task.sort_order is not None else ti,
                            task.validation_mode,
                            Json(task.scoring) if task.scoring else None,
                            Json(task.metadata) if task.metadata else None,
                        ),
                    )

                    for vi, validator in enumerate(task.validators):
                        cur.execute(
                            """
                            INSERT INTO task_validators
                                (validator_id, task_id, type, mode, config_json,
                                 expected_json, timeout_seconds, sort_order, enabled)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                            """,
                            (
                                _new_id("val"),
                                task_id,
                                validator.type,
                                validator.mode,
                                Json(validator.config()),
                                Json(validator.expect.model_dump(exclude_none=True))
                                if validator.expect
                                else None,
                                validator.timeout_seconds
                                if validator.timeout_seconds is not None
                                else 30,
                                vi,
                            ),
                        )

                    for hi, hint in enumerate(task.hints):
                        cur.execute(
                            """
                            INSERT INTO task_hints
                                (hint_id, task_id, sort_order, title, body_md, penalty_points)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                _new_id("hint"),
                                task_id,
                                hi,
                                hint.title,
                                hint.body_md,
                                hint.penalty_points,
                            ),
                        )

        return {
            "pack_id": pack_id,
            "pack_version_id": pack_version_id,
            "status": "imported",
            "counts": counts,
        }
