"""Quest pack manifest linter.

Produces actionable, structured findings without touching the database. Used by
``POST /api/host/quest-packs/lint`` and as a gate before import. Findings are
either ``error`` (blocks import) or ``warning`` (allowed, but worth flagging).

The linter does three passes:
1. YAML parse (syntax).
2. Pydantic structural validation (required fields / types).
3. Semantic rules (unique slugs, references, validators, operators, template
   variables).
"""

import re
from typing import Any, Dict, List, Optional, Tuple

import yaml

from models.quest_pack import (
    KNOWN_EXPECT_OPERATORS,
    KNOWN_VALIDATOR_TYPES,
    SUPPORTED_SCHEMA_VERSIONS,
    QuestPackManifest,
)

# Server-resolved template variables permitted in manifest strings.
SUPPORTED_TEMPLATE_VARS = {
    "event_id",
    "event_slug",
    "team_id",
    "team_slug",
    "team_prefix",
    "team_catalog",
    "team_schema",
    "event_catalog",
    "event_schema",
    "event_start",
    "event_end",
    "current_user",
    "team_members",
}

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TEMPLATE_VAR_RE = re.compile(r"\$\{([a-zA-Z0-9_]+)\}")


class LintResult:
    """Collects lint findings and a content summary."""

    def __init__(self) -> None:
        self.errors: List[Dict[str, str]] = []
        self.warnings: List[Dict[str, str]] = []
        self.summary: Dict[str, int] = {
            "quests": 0,
            "tasks": 0,
            "validators": 0,
            "hints": 0,
        }
        self.manifest: Optional[QuestPackManifest] = None

    def error(self, loc: str, message: str) -> None:
        self.errors.append({"loc": loc, "message": message})

    def warn(self, loc: str, message: str) -> None:
        self.warnings.append({"loc": loc, "message": message})

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "summary": self.summary,
        }


def _check_template_vars(result: LintResult, loc: str, text: Optional[str]) -> None:
    if not text or not isinstance(text, str):
        return
    for var in _TEMPLATE_VAR_RE.findall(text):
        if var not in SUPPORTED_TEMPLATE_VARS:
            result.warn(
                loc,
                f"Unknown template variable '${{{var}}}'. Supported: "
                + ", ".join(sorted(SUPPORTED_TEMPLATE_VARS)),
            )


def lint_manifest_text(manifest_yaml: str) -> LintResult:
    """Lint a raw YAML manifest string and return a :class:`LintResult`."""
    result = LintResult()

    # ── Pass 1: YAML syntax ──────────────────────────────────────────────────
    try:
        raw = yaml.safe_load(manifest_yaml)
    except yaml.YAMLError as exc:
        loc = "yaml"
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            loc = f"yaml:line {mark.line + 1}, col {mark.column + 1}"
        result.error(loc, f"Invalid YAML: {getattr(exc, 'problem', str(exc))}")
        return result

    if not isinstance(raw, dict):
        result.error("root", "Manifest must be a YAML mapping at the top level.")
        return result

    # ── Pass 2: structural validation via Pydantic ───────────────────────────
    try:
        manifest = QuestPackManifest.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError (avoid hard import)
        errors = getattr(exc, "errors", None)
        if callable(errors):
            for err in exc.errors():  # type: ignore[attr-defined]
                loc = ".".join(str(p) for p in err.get("loc", [])) or "root"
                result.error(loc, err.get("msg", "invalid value"))
        else:
            result.error("root", f"Manifest structure invalid: {exc}")
        return result

    result.manifest = manifest
    result.summary = manifest.counts()

    # ── Pass 3: semantic rules ────────────────────────────────────────────────
    if manifest.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        result.warn(
            "schema_version",
            f"Unsupported schema_version '{manifest.schema_version}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_SCHEMA_VERSIONS))}",
        )

    if not _SLUG_RE.match(manifest.pack.slug):
        result.error(
            "pack.slug",
            f"Invalid slug '{manifest.pack.slug}'. Use lowercase letters, "
            "digits, and single dashes (e.g. 'ai-bi-gameday').",
        )

    if not manifest.pack.version.strip():
        result.error("pack.version", "pack.version must not be empty.")

    if not manifest.quests:
        result.error("quests", "A quest pack must define at least one quest.")

    quest_slugs: set = set()
    for qi, quest in enumerate(manifest.quests):
        qloc = f"quests[{qi}]({quest.slug})"

        if not _SLUG_RE.match(quest.slug):
            result.error(f"{qloc}.slug", f"Invalid quest slug '{quest.slug}'.")
        if quest.slug in quest_slugs:
            result.error(f"{qloc}.slug", f"Duplicate quest slug '{quest.slug}'.")
        quest_slugs.add(quest.slug)
        _check_template_vars(result, f"{qloc}.narrative_md", quest.narrative_md)

        if not quest.tasks:
            result.error(f"{qloc}.tasks", "Each quest must define at least one task.")

        task_slugs: set = set()
        for ti, task in enumerate(quest.tasks):
            tloc = f"{qloc}.tasks[{ti}]({task.slug})"

            if not _SLUG_RE.match(task.slug):
                result.error(f"{tloc}.slug", f"Invalid task slug '{task.slug}'.")
            if task.slug in task_slugs:
                result.error(
                    f"{tloc}.slug",
                    f"Duplicate task slug '{task.slug}' within quest '{quest.slug}'.",
                )
            task_slugs.add(task.slug)

            if task.points < 0:
                result.error(f"{tloc}.points", "points must be >= 0.")

            _check_template_vars(result, f"{tloc}.instructions_md", task.instructions_md)

            # Validators required unless the task is explicitly manual.
            has_manual = any(v.type == "manual" for v in task.validators)
            if not task.validators and not task.manual_validation_required:
                result.error(
                    f"{tloc}.validators",
                    "Task has no validators. Add at least one validator or set "
                    "manual_validation_required: true.",
                )

            validator_ids: set = set()
            for vi, validator in enumerate(task.validators):
                vloc = f"{tloc}.validators[{vi}]({validator.id})"
                if validator.id in validator_ids:
                    result.error(
                        f"{vloc}.id",
                        f"Duplicate validator id '{validator.id}' within task.",
                    )
                validator_ids.add(validator.id)

                if validator.type not in KNOWN_VALIDATOR_TYPES:
                    result.warn(
                        f"{vloc}.type",
                        f"Unknown validator type '{validator.type}'. Known types: "
                        + ", ".join(sorted(KNOWN_VALIDATOR_TYPES)),
                    )

                if validator.mode not in {"sync", "async"}:
                    result.error(
                        f"{vloc}.mode",
                        f"Validator mode must be 'sync' or 'async', got '{validator.mode}'.",
                    )

                _lint_validator_config(result, vloc, validator)

            for hi, hint in enumerate(task.hints):
                hloc = f"{tloc}.hints[{hi}]"
                if hint.penalty_points > 0:
                    result.warn(
                        f"{hloc}.penalty_points",
                        "penalty_points is usually <= 0 (a penalty). "
                        f"Got {hint.penalty_points}.",
                    )

    # Unlock-rule references must point at real quest slugs.
    for qi, quest in enumerate(manifest.quests):
        rule = quest.unlock_rule
        if rule and rule.type == "quest_completed":
            qloc = f"quests[{qi}]({quest.slug}).unlock_rule"
            if not rule.quest_slug:
                result.error(
                    f"{qloc}.quest_slug",
                    "unlock_rule type 'quest_completed' requires quest_slug.",
                )
            elif rule.quest_slug not in quest_slugs:
                result.error(
                    f"{qloc}.quest_slug",
                    f"unlock_rule references unknown quest '{rule.quest_slug}'.",
                )
            elif rule.quest_slug == quest.slug:
                result.error(
                    f"{qloc}.quest_slug",
                    "A quest cannot be gated on completing itself.",
                )

    return result


def _lint_validator_config(result: LintResult, vloc: str, validator: Any) -> None:
    """Type-specific validator checks."""
    vtype = validator.type
    cfg = validator.config()

    if vtype == "sql_assertion":
        statement = cfg.get("statement")
        if not statement:
            result.error(f"{vloc}.statement", "sql_assertion requires a 'statement'.")
        else:
            _check_template_vars(result, f"{vloc}.statement", statement)
        expect = validator.expect
        if expect is None:
            result.warn(
                f"{vloc}.expect",
                "sql_assertion has no 'expect' block; result will not be checked.",
            )
        elif expect.operator and expect.operator not in KNOWN_EXPECT_OPERATORS:
            result.error(
                f"{vloc}.expect.operator",
                f"Unknown operator '{expect.operator}'. Supported: "
                + ", ".join(sorted(KNOWN_EXPECT_OPERATORS)),
            )
    elif vtype == "databricks_sdk":
        if not cfg.get("check"):
            result.error(f"{vloc}.check", "databricks_sdk requires a 'check' name.")
    elif vtype == "system_table":
        if not cfg.get("table"):
            result.error(f"{vloc}.table", "system_table validator requires 'table'.")
        if not cfg.get("condition"):
            result.warn(f"{vloc}.condition", "system_table validator has no 'condition'.")
    elif vtype == "notebook":
        if not cfg.get("notebook_path"):
            result.error(
                f"{vloc}.notebook_path", "notebook validator requires 'notebook_path'."
            )
