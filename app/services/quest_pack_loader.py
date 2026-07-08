"""Quest pack loader: lint + content-hash + import orchestration.

Sits between the host API and the repository. Linting runs first; import is
refused unless the manifest is error-free. Content hashing is computed from the
canonical (key-sorted) manifest so reformatting does not create a new version,
but any content change does.
"""

import hashlib
import json
import logging
from typing import Any, Dict, Optional

from models.quest_pack import QuestPackManifest
from repositories.quest_packs import ImmutableVersionError, QuestPacksRepository
from services import audit
from services.quest_pack_linter import lint_manifest_text

logger = logging.getLogger("databricks-quest.services.quest_pack_loader")


class QuestPackImportError(Exception):
    """Raised when a manifest cannot be imported (lint errors or conflict)."""

    def __init__(self, message: str, lint: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.lint = lint


def compute_content_hash(manifest: QuestPackManifest) -> str:
    """Stable SHA-256 over the canonical manifest content."""
    canonical = json.dumps(
        manifest.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lint_text(manifest_yaml: str) -> Dict[str, Any]:
    """Lint a YAML manifest string; never raises for content problems."""
    return lint_manifest_text(manifest_yaml).to_dict()


def import_text(manifest_yaml: str, actor: Optional[str]) -> Dict[str, Any]:
    """Lint then import a YAML manifest.

    Raises :class:`QuestPackImportError` if linting fails or the version is
    immutable-conflicting. On success returns pack/version ids, status,
    counts, content hash, and any non-blocking warnings.
    """
    result = lint_manifest_text(manifest_yaml)
    if not result.ok or result.manifest is None:
        raise QuestPackImportError(
            "Quest pack failed validation; fix the reported errors and retry.",
            lint=result.to_dict(),
        )

    manifest = result.manifest
    content_hash = compute_content_hash(manifest)
    repo = QuestPacksRepository()

    try:
        outcome = repo.import_manifest(manifest, content_hash, actor)
    except ImmutableVersionError as exc:
        raise QuestPackImportError(str(exc), lint=result.to_dict()) from exc

    outcome["content_hash"] = content_hash
    outcome["warnings"] = result.warnings

    # Audit the import (best-effort; never blocks the operation).
    audit.record_audit(
        action="quest_pack.import",
        actor_user_id=actor,
        target_type="quest_pack_version",
        target_id=outcome.get("pack_version_id"),
        payload={
            "slug": manifest.pack.slug,
            "version": manifest.pack.version,
            "status": outcome.get("status"),
            "counts": outcome.get("counts"),
            "content_hash": content_hash,
        },
    )
    return outcome


def load_text_from_file(path: str) -> str:
    """Read a manifest YAML file from disk."""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()
