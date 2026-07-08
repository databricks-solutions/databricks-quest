"""Event resource namespacing + safety guards (PR08).

This module is the **single authority** for deciding which Databricks resources
an event is allowed to create or destroy. It is intentionally pure (no DB, no
SDK) so the guard logic is exhaustively unit-testable and can never be bypassed
by a bug in an executor.

The contract enforced here implements the non-negotiable from ``AGENTS.md``:

    Any bootstrap/reset/destructive action must be restricted to an event,
    team, user, catalog, schema, or workspace path prefix created for that
    event.

A reset can therefore only ever ``DROP`` a schema that this event's own
namespace computes — never ``system``, ``main``, ``hive_metastore``, a bare
catalog, a wildcard, or another event's schema.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Catalogs that an event may never create in or drop, even if mis-configured.
RESERVED_CATALOGS = frozenset(
    {
        "system",
        "hive_metastore",
        "samples",
        "main",
        "information_schema",
        "__databricks_internal",
    }
)

# A valid unquoted Databricks identifier for our purposes: letters, digits, and
# underscores only. We deliberately forbid dots, spaces, quotes, semicolons, and
# wildcards so a name can never smuggle a second object or a statement break.
_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


class NamespaceError(Exception):
    """Raised when a resource target is outside the event's safe namespace."""

    def __init__(self, message: str, code: str = "UNSAFE_NAMESPACE"):
        super().__init__(message)
        self.code = code


def sanitize_identifier(raw: str) -> str:
    """Coerce arbitrary text into a safe lowercase identifier fragment.

    Non-alphanumerics collapse to single underscores; leading/trailing
    underscores are trimmed. Used to derive default catalog/schema names from an
    event slug or team name. Never used to *validate* host-supplied names — that
    is :func:`assert_valid_identifier`'s job.
    """
    s = re.sub(r"[^a-z0-9]+", "_", (raw or "").strip().lower()).strip("_")
    return s or "x"


def assert_valid_identifier(name: str, *, kind: str = "identifier") -> str:
    """Return ``name`` if it is a safe unquoted identifier, else raise.

    Blocks the empty string, dotted names, wildcards, and anything with
    whitespace/quotes/semicolons — the building blocks of namespace escape or
    SQL injection through an identifier slot.
    """
    if not name or not _IDENT_RE.match(name):
        raise NamespaceError(
            f"Unsafe {kind}: {name!r}. Only letters, digits, and underscores are allowed.",
            code="INVALID_IDENTIFIER",
        )
    return name


def event_namespace(event: Dict[str, Any]) -> Dict[str, str]:
    """Compute the catalog + schema-prefix an event owns.

    Resolution order:
    1. ``event.config_json.resource_namespace`` (host-configured), then
    2. a deterministic default derived from the event slug: catalog
       ``quest_<slug>`` and schema prefix ``team_``.

    The returned catalog is validated and must not be a reserved catalog — an
    event may not provision into ``main``/``system``/etc.
    """
    cfg = event.get("config_json") or {}
    ns = (cfg.get("resource_namespace") if isinstance(cfg, dict) else None) or {}

    slug = event.get("slug") or event.get("event_id") or "event"
    catalog = (ns.get("catalog") or f"quest_{sanitize_identifier(slug)}").strip()
    schema_prefix = (ns.get("schema_prefix") or "team_").strip()

    assert_valid_identifier(catalog, kind="catalog")
    if catalog.lower() in RESERVED_CATALOGS:
        raise NamespaceError(
            f"Refusing to use reserved catalog {catalog!r} for event resources.",
            code="RESERVED_CATALOG",
        )
    # The prefix must itself be a safe fragment (it is concatenated with a team
    # name to form a schema identifier).
    assert_valid_identifier(schema_prefix.rstrip("_") or "team", kind="schema_prefix")
    return {"catalog": catalog, "schema_prefix": schema_prefix}


def team_target(event: Dict[str, Any], team: Dict[str, Any]) -> Dict[str, str]:
    """Compute the (catalog, schema, fqn) a team's resources live under.

    Honours an explicit ``team_catalog``/``team_schema`` on the team row, else
    derives them from the event namespace and the team name. Every component is
    validated, so the result is always safe to interpolate into DDL.
    """
    ns = event_namespace(event)
    catalog = (team.get("team_catalog") or ns["catalog"]).strip()
    schema = (
        team.get("team_schema")
        or f"{ns['schema_prefix']}{sanitize_identifier(team.get('name') or team.get('team_id') or 'team')}"
    ).strip()

    assert_valid_identifier(catalog, kind="catalog")
    assert_valid_identifier(schema, kind="schema")
    if catalog.lower() in RESERVED_CATALOGS:
        raise NamespaceError(
            f"Refusing to target reserved catalog {catalog!r}.", code="RESERVED_CATALOG"
        )
    return {
        "team_id": team.get("team_id"),
        "team_name": team.get("name"),
        "catalog": catalog,
        "schema": schema,
        "fqn": f"{catalog}.{schema}",
    }


def team_targets(event: Dict[str, Any], teams: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    return [team_target(event, t) for t in teams]


def assert_within_namespace(
    fqn: str, event: Dict[str, Any], teams: List[Dict[str, Any]]
) -> None:
    """Raise unless ``fqn`` (``catalog.schema``) is one of the event's team targets.

    This is the destructive-action gate: reset builds its DROP targets from the
    same :func:`team_targets`, so a target that is not an exact, current team
    schema (or whose catalog/schema is malformed) is refused before any SQL is
    emitted.
    """
    parts = (fqn or "").split(".")
    if len(parts) != 2:
        raise NamespaceError(
            f"Refusing to operate on {fqn!r}: expected a catalog.schema target.",
            code="NOT_A_SCHEMA",
        )
    catalog, schema = parts[0].strip(), parts[1].strip()
    assert_valid_identifier(catalog, kind="catalog")
    assert_valid_identifier(schema, kind="schema")
    if catalog.lower() in RESERVED_CATALOGS:
        raise NamespaceError(
            f"Refusing to operate on reserved catalog {catalog!r}.", code="RESERVED_CATALOG"
        )
    allowed = {t["fqn"] for t in team_targets(event, teams)}
    if fqn not in allowed:
        raise NamespaceError(
            f"{fqn!r} is outside this event's resource namespace; refusing to touch it.",
            code="OUTSIDE_NAMESPACE",
        )


# ── Templating for pack-supplied seed SQL ────────────────────────────────────

_ALLOWED_SLOTS = {"team_catalog", "team_schema", "event_id"}
_SLOT_RE = re.compile(r"\$\{([a-zA-Z_]+)\}")


def render_seed_sql(template: str, target: Dict[str, str], event_id: str) -> str:
    """Resolve ``${team_catalog}``/``${team_schema}``/``${event_id}`` in seed SQL.

    Only those three slots are allowed; any other ``${...}`` slot raises so a
    pack can't reference an unresolved (and therefore unguarded) name. Values
    come from the validated :func:`team_target`, so the rendered SQL can only
    point at the team's own schema.
    """
    values = {
        "team_catalog": target["catalog"],
        "team_schema": target["schema"],
        "event_id": event_id,
    }

    def _sub(m: "re.Match[str]") -> str:
        slot = m.group(1)
        if slot not in _ALLOWED_SLOTS:
            raise NamespaceError(
                f"Unknown template slot ${{{slot}}} in seed SQL.", code="BAD_SEED_SLOT"
            )
        return values[slot]

    return _SLOT_RE.sub(_sub, template)
