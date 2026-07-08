"""Quest-pack import negatives (PR-pilot G).

Covers the three rejection paths the host import endpoint must surface cleanly:

- invalid YAML / lint errors → :class:`QuestPackImportError` carrying the lint
  report, *before* any DB write;
- re-import of identical content for an existing version → ``duplicate`` (no-op);
- re-import of the same version with *changed* content → ``ImmutableVersionError``
  (versions are immutable; the author must bump ``pack.version``).

The duplicate / immutable branches are exercised against the repository with its
``find_pack_by_slug`` / ``find_version`` lookups stubbed, so no live Postgres is
needed and ``db.transaction`` is never reached.
"""

import pytest

from repositories.quest_packs import ImmutableVersionError, QuestPacksRepository
from services.quest_pack_loader import (
    QuestPackImportError,
    compute_content_hash,
    import_text,
)
from services.quest_pack_linter import lint_manifest_text


_VALID_PACK = """
schema_version: "1.0"
pack:
  slug: test-pilot-pack
  title: Test Pilot Pack
  version: 1.0.0
  owner: pilot@databricks.com
learning_objectives:
  - Verify the import path
quests:
  - slug: q1
    title: First quest
    sort_order: 1
    tasks:
      - slug: t1
        title: Count rows
        objective: Confirm the gold table has rows
        points: 100
        validators:
          - id: v1
            type: manual
        hints:
          - title: Tip
            body_md: Look at the gold table
            penalty_points: -10
"""


def _manifest(raw=_VALID_PACK):
    result = lint_manifest_text(raw)
    assert result.ok, result.errors
    return result.manifest


# ── invalid YAML / lint errors are refused before any DB write ───────────────


def test_import_invalid_yaml_raises_before_db(monkeypatch):
    # If linting fails, import_text must raise without ever touching the repo.
    def boom(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("repo.import_manifest must not run on lint failure")

    monkeypatch.setattr(QuestPacksRepository, "import_manifest", boom)

    with pytest.raises(QuestPackImportError) as exc:
        import_text("this: is: not: a: valid: pack", actor="host@x")
    # The lint report rides along for the host to see.
    assert exc.value.lint is not None


def test_import_missing_required_fields_raises():
    bad = """
pack:
  slug: missing-quests
  title: No quests
  version: 1.0.0
"""
    with pytest.raises(QuestPackImportError):
        import_text(bad, actor="host@x")


# ── duplicate content is a no-op ─────────────────────────────────────────────


def test_reimport_identical_content_is_duplicate(monkeypatch):
    manifest = _manifest()
    content_hash = compute_content_hash(manifest)

    monkeypatch.setattr(
        QuestPacksRepository, "find_pack_by_slug",
        lambda self, slug: {"pack_id": "pack_1"},
    )
    monkeypatch.setattr(
        QuestPacksRepository, "find_version",
        lambda self, pack_id, version: {
            "pack_version_id": "pv_1",
            "content_hash": content_hash,  # identical → duplicate
        },
    )
    # Never reaches db.transaction for the duplicate branch.
    out = QuestPacksRepository().import_manifest(manifest, content_hash, actor="host@x")
    assert out["status"] == "duplicate"
    assert out["pack_version_id"] == "pv_1"


# ── same version, changed content → immutable conflict ───────────────────────


def test_reimport_same_version_changed_content_is_immutable(monkeypatch):
    manifest = _manifest()
    content_hash = compute_content_hash(manifest)

    monkeypatch.setattr(
        QuestPacksRepository, "find_pack_by_slug",
        lambda self, slug: {"pack_id": "pack_1"},
    )
    monkeypatch.setattr(
        QuestPacksRepository, "find_version",
        lambda self, pack_id, version: {
            "pack_version_id": "pv_1",
            "content_hash": "deadbeef-different",  # changed → immutable conflict
        },
    )
    with pytest.raises(ImmutableVersionError):
        QuestPacksRepository().import_manifest(manifest, content_hash, actor="host@x")


def test_import_text_maps_immutable_to_import_error(monkeypatch):
    # import_text should wrap ImmutableVersionError as a QuestPackImportError so
    # the host endpoint returns a clean 409-style envelope.
    def raise_immutable(self, manifest, content_hash, actor):
        raise ImmutableVersionError("version exists with different content")

    monkeypatch.setattr(QuestPacksRepository, "import_manifest", raise_immutable)
    with pytest.raises(QuestPackImportError):
        import_text(_VALID_PACK, actor="host@x")


# ── content hash is stable across reformatting, changes on edit ──────────────


def test_content_hash_changes_when_content_changes():
    base = _manifest()
    edited_raw = _VALID_PACK.replace("points: 100", "points: 250")
    edited = _manifest(edited_raw)
    assert compute_content_hash(base) != compute_content_hash(edited)
