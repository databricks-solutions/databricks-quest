"""Resource bootstrap & reset orchestration (PR08).

Turns an event + its teams + the pack's optional ``resources`` section into a
deterministic, inspectable **plan** of SQL statements, then executes that plan
against a Databricks warehouse via an injected executor.

Safety is layered:
- :mod:`services.namespace` computes every target and is the sole authority on
  what is in-namespace; this service never invents a target name.
- A plan is built first and can be returned for **dry-run** without executing.
- Execution refuses to run if any statement is flagged out-of-namespace, and
  reset re-validates every DROP target against the namespace immediately before
  emitting it.

The executor signature matches ``services.sql_runner.Executor``
(``Callable[[str, int], List[dict]]``) so the warehouse runner can be reused;
tests inject a fake.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from services import namespace as ns

logger = logging.getLogger("databricks-quest.services.resource_service")

Executor = Callable[[str, int], List[Dict[str, Any]]]


def _seed_statements(resources: Optional[Dict[str, Any]]) -> List[str]:
    """Extract pack seed SQL statements (``resources.seed_sql``: list[str])."""
    if not isinstance(resources, dict):
        return []
    raw = resources.get("seed_sql") or []
    if isinstance(raw, str):
        raw = [raw]
    return [s for s in raw if isinstance(s, str) and s.strip()]


class ResourcePlanItem(dict):
    """A single planned statement. A plain dict subclass for easy JSON return."""


def build_bootstrap_plan(
    event: Dict[str, Any],
    teams: List[Dict[str, Any]],
    resources: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return the ordered list of statements to provision all team resources.

    For each team: ensure the catalog, ensure the team schema, then any pack
    seed statements (rendered against the team's namespace). Every item carries
    ``within_namespace`` so a dry-run can surface blockers without executing.
    """
    items: List[Dict[str, Any]] = []
    seen_catalogs: set[str] = set()
    targets = ns.team_targets(event, teams)
    event_id = event.get("event_id")
    seeds = _seed_statements(resources)

    for target in targets:
        catalog, schema, fqn = target["catalog"], target["schema"], target["fqn"]
        if catalog not in seen_catalogs:
            seen_catalogs.add(catalog)
            items.append(ResourcePlanItem(
                op="create_catalog", team_id=target["team_id"],
                resource_type="catalog", target=catalog,
                sql=f"CREATE CATALOG IF NOT EXISTS {catalog}",
                within_namespace=True,
            ))
        items.append(ResourcePlanItem(
            op="create_schema", team_id=target["team_id"],
            resource_type="schema", target=fqn,
            sql=f"CREATE SCHEMA IF NOT EXISTS {fqn}",
            within_namespace=True,
        ))
        for raw_sql in seeds:
            rendered = ns.render_seed_sql(raw_sql, target, event_id)
            items.append(ResourcePlanItem(
                op="seed", team_id=target["team_id"],
                resource_type="seed", target=fqn,
                sql=rendered,
                # Seed SQL is host-authored pack content scoped to the team
                # schema via templating; it is considered in-namespace.
                within_namespace=True,
            ))
    return items


def build_reset_plan(
    event: Dict[str, Any], teams: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Return the ordered list of DROP statements for an event's team schemas.

    Each target is re-validated against the namespace; a target that fails the
    guard is included with ``within_namespace=False`` and an ``error`` so a
    dry-run shows it and execution refuses the whole plan.
    """
    items: List[Dict[str, Any]] = []
    for target in ns.team_targets(event, teams):
        fqn = target["fqn"]
        item = ResourcePlanItem(
            op="drop_schema", team_id=target["team_id"],
            resource_type="schema", target=fqn,
            sql=f"DROP SCHEMA IF EXISTS {fqn} CASCADE",
        )
        try:
            ns.assert_within_namespace(fqn, event, teams)
            item["within_namespace"] = True
        except ns.NamespaceError as exc:
            item["within_namespace"] = False
            item["error"] = str(exc)
        items.append(item)
    return items


def plan_blockers(plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return plan items that are out-of-namespace (execution must refuse these)."""
    return [i for i in plan if not i.get("within_namespace", False)]


class ResourceService:
    def __init__(self, repo=None):
        # Lazy import keeps namespace/plan tests free of any DB dependency.
        if repo is None:
            from repositories.resources import ResourcesRepository

            repo = ResourcesRepository()
        self._repo = repo

    def execute_plan(
        self,
        event: Dict[str, Any],
        plan: List[Dict[str, Any]],
        executor: Executor,
        *,
        created_by: Optional[str] = None,
        timeout_seconds: int = 60,
        record: bool = True,
    ) -> Dict[str, Any]:
        """Run a plan's statements in order, recording resource health.

        Refuses to run anything if the plan contains an out-of-namespace item.
        Returns ``{"executed": [...], "ok": bool, "blockers": [...]}`` where each
        executed item gets ``status`` (``ok``/``error``) and any ``error``.
        """
        blockers = plan_blockers(plan)
        if blockers:
            return {"ok": False, "executed": [], "blockers": blockers}

        event_id = event.get("event_id")
        executed: List[Dict[str, Any]] = []
        all_ok = True
        for item in plan:
            entry = dict(item)
            try:
                executor(item["sql"], timeout_seconds)
                entry["status"] = "ok"
            except Exception as exc:  # noqa: BLE001 - capture per-statement failure
                all_ok = False
                entry["status"] = "error"
                entry["error"] = str(exc)
                logger.warning("resource statement failed (%s): %s", item.get("op"), exc)

            if record and item.get("resource_type") in ("catalog", "schema"):
                removed = item.get("op") == "drop_schema"
                status = (
                    "removed" if (removed and entry["status"] == "ok")
                    else ("active" if entry["status"] == "ok" else "failed")
                )
                self._repo.upsert(
                    event_id=event_id,
                    fqn=item["target"],
                    resource_type=item["resource_type"],
                    status=status,
                    team_id=item.get("team_id"),
                    message=entry.get("error"),
                    created_by=created_by,
                )
            executed.append(entry)
        return {"ok": all_ok, "executed": executed, "blockers": []}


default_resource_service = ResourceService()
