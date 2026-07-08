"""Guardrail tests for the shipped sample quest packs (PR09).

These assert the built-in packs always lint cleanly (no errors *and* no
warnings) and meet the PR09 content bar (>=2 packs, each >=3 quests / >=6 tasks,
SQL + manual validators, >=1 databricks_sdk validator, hints, facilitator
notes). DB-free: lint + manifest parsing only.
"""

import glob
import os

import pytest

from models.quest_pack import QuestPackManifest
from services.quest_pack_linter import lint_manifest_text
from services.quest_pack_loader import compute_content_hash

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_DIR = os.path.join(REPO_ROOT, "samples", "packs")


def _pack_paths():
    return sorted(glob.glob(os.path.join(PACK_DIR, "*.yml")))


def test_at_least_two_sample_packs_shipped():
    assert len(_pack_paths()) >= 2


@pytest.mark.parametrize("path", _pack_paths(), ids=lambda p: os.path.basename(p))
def test_sample_pack_lints_with_no_errors_or_warnings(path):
    with open(path, "r", encoding="utf-8") as fh:
        result = lint_manifest_text(fh.read())
    assert result.ok, f"{path} lint errors: {result.errors}"
    assert result.warnings == [], f"{path} lint warnings: {result.warnings}"


@pytest.mark.parametrize("path", _pack_paths(), ids=lambda p: os.path.basename(p))
def test_sample_pack_meets_content_bar(path):
    with open(path, "r", encoding="utf-8") as fh:
        result = lint_manifest_text(fh.read())
    m = result.manifest
    assert m is not None
    assert len(m.quests) >= 3
    tasks = [t for q in m.quests for t in q.tasks]
    assert len(tasks) >= 6
    validators = [v for t in tasks for v in t.validators]
    types = {v.type for v in validators}
    assert "sql_assertion" in types
    assert "manual" in types
    assert "databricks_sdk" in types  # PR09: include an SDK validator
    # Every task has hints, and the pack carries learning objectives + resources.
    assert all(t.hints for t in tasks)
    assert m.learning_objectives
    assert m.resources and m.resources.get("seed_sql")
    assert any(q.facilitator_notes for q in m.quests)


@pytest.mark.parametrize("path", _pack_paths(), ids=lambda p: os.path.basename(p))
def test_sample_pack_content_hash_is_stable(path):
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    m1 = QuestPackManifest.model_validate(lint_manifest_text(raw).manifest.model_dump())
    h1 = compute_content_hash(lint_manifest_text(raw).manifest)
    h2 = compute_content_hash(m1)
    assert h1 == h2


def test_sample_packs_have_distinct_slugs():
    slugs = []
    for path in _pack_paths():
        with open(path, "r", encoding="utf-8") as fh:
            slugs.append(lint_manifest_text(fh.read()).manifest.pack.slug)
    assert len(slugs) == len(set(slugs))
