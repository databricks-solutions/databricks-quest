"""Pydantic models for the Quest Pack manifest (schema_version 1.0).

These mirror the authoring format documented in ``samples/QUEST_PACK_SCHEMA.md``
and ``docs/06_QUEST_MODEL_AND_VALIDATION_ENGINE.md``. Models are intentionally
permissive (``extra="allow"``) so type-specific validator config and
forward-compatible fields survive a round trip and can be persisted as JSON;
the linter (``services/quest_pack_linter.py``) applies the stricter semantic
rules and produces actionable messages.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# Validator types recognised by the authoring format. Execution arrives in the
# validation engine PR; here it only drives lint warnings for unknown types.
KNOWN_VALIDATOR_TYPES = {
    "sql_assertion",
    "databricks_sdk",
    "workspace_api",
    "system_table",
    "notebook",
    "python_code",
    "rest_api",
    "manual",
}

# Operators supported by sql_assertion `expect` blocks.
KNOWN_EXPECT_OPERATORS = {
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "contains",
    "not_contains",
    "is_true",
    "is_false",
}

SUPPORTED_SCHEMA_VERSIONS = {"1.0"}


class Expectation(BaseModel):
    """Expected-result block for a validator (e.g. sql_assertion)."""

    model_config = ConfigDict(extra="allow")

    operator: Optional[str] = None
    value: Optional[Any] = None
    min_rows: Optional[int] = None


class ValidatorSpec(BaseModel):
    """A single validator on a task.

    Only ``id``/``type``/``mode`` are structural; everything else is
    type-specific config that is retained and persisted as ``config_json``.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    mode: str = "sync"
    expect: Optional[Expectation] = None
    timeout_seconds: Optional[int] = None

    # Core, non-config keys that should NOT be folded into config_json.
    _CORE_KEYS = {"id", "type", "mode", "expect", "timeout_seconds"}

    def config(self) -> Dict[str, Any]:
        """Return the type-specific config (everything but the core keys)."""
        data = self.model_dump(exclude_none=True)
        return {k: v for k, v in data.items() if k not in self._CORE_KEYS}


class HintSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    body_md: str
    title: Optional[str] = None
    penalty_points: int = 0


class UnlockRule(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = "always"
    quest_slug: Optional[str] = None


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str
    title: str
    objective: str
    instructions_md: Optional[str] = None
    success_criteria_md: Optional[str] = None
    points: int = 0
    sort_order: Optional[int] = None
    validation_mode: str = "auto"
    manual_validation_required: bool = False
    validators: List[ValidatorSpec] = Field(default_factory=list)
    hints: List[HintSpec] = Field(default_factory=list)
    scoring: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class QuestSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str
    title: str
    narrative_md: Optional[str] = None
    category: Optional[str] = None
    difficulty: Optional[str] = None
    base_points: int = 0
    sort_order: Optional[int] = None
    unlock_rule: Optional[UnlockRule] = None
    facilitator_notes: Optional[str] = None
    tasks: List[TaskSpec] = Field(default_factory=list)


class PackMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str
    title: str
    version: str
    description: Optional[str] = None
    audience: Optional[List[str]] = None
    duration_minutes: Optional[int] = None
    difficulty: Optional[str] = None
    owner: Optional[str] = None


class QuestPackManifest(BaseModel):
    """Top-level quest pack manifest."""

    model_config = ConfigDict(extra="allow")

    schema_version: str
    pack: PackMeta
    scenario: Optional[Dict[str, Any]] = None
    learning_objectives: Optional[List[str]] = None
    capabilities_required: Optional[List[str]] = None
    resources: Optional[Dict[str, Any]] = None
    scoring_defaults: Optional[Dict[str, Any]] = None
    quests: List[QuestSpec] = Field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        """Return quest/task/validator/hint counts for import summaries."""
        tasks = [t for q in self.quests for t in q.tasks]
        validators = [v for t in tasks for v in t.validators]
        hints = [h for t in tasks for h in t.hints]
        return {
            "quests": len(self.quests),
            "tasks": len(tasks),
            "validators": len(validators),
            "hints": len(hints),
        }
