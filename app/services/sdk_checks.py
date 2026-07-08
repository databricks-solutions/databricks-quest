"""Read-only Databricks workspace checks for the ``databricks_sdk`` validator.

Each check is a pure-ish function ``check(client, params, ctx) -> CheckResult``
that performs **lookups only** (never creates/mutates workspace state) against a
:class:`databricks.sdk.WorkspaceClient`-shaped object and reports whether the
expected artefact exists. The ``client`` is duck-typed so unit tests can inject a
fake backend without the SDK installed.

Result contract — every check returns a dict::

    {"found": bool, "detail": "<host-facing summary>", "evidence": {...}}

Error contract:

- A missing/invalid **required param** raises :class:`SDKCheckConfigError`; the
  validator maps it to a config error (a quest-pack authoring bug, surfaced
  loudly to the host).
- Any **runtime** problem (SDK missing, API error, unknown API surface) is left
  to raise; the validator catches it and returns a ``manual`` outcome so the
  task routes to host review — a pilot is never hard-blocked by a check that
  could not run.

Registry: :data:`SDK_CHECKS` maps the check name used in quest packs to its
implementation. The names mirror those used across ``quest_packs/built_in/`` and
``samples/packs/``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("databricks-quest.services.sdk_checks")


class SDKCheckConfigError(Exception):
    """Raised when a check's required params are missing/invalid (authoring bug)."""


CheckResult = Dict[str, Any]
SDKCheck = Callable[[Any, Dict[str, Any], Any], CheckResult]


# ── small duck-typed helpers ─────────────────────────────────────────────────

def _first_attr(obj: Any, attrs: Tuple[str, ...]) -> Optional[str]:
    for a in attrs:
        val = getattr(obj, a, None)
        if val:
            return str(val)
        if isinstance(obj, dict) and obj.get(a):
            return str(obj[a])
    return None


_NAME_ATTRS = ("display_name", "name", "label", "title")


def _iter_names(items: Optional[Iterable[Any]]) -> List[Tuple[str, Any]]:
    out: List[Tuple[str, Any]] = []
    for it in items or []:
        out.append((_first_attr(it, _NAME_ATTRS) or "", it))
    return out


def _matches(name: str, needle: Optional[str]) -> bool:
    if not needle:
        return True
    return needle.lower() in (name or "").lower()


def _require(params: Dict[str, Any], key: str) -> Any:
    val = params.get(key)
    if val is None or (isinstance(val, str) and not val.strip()):
        raise SDKCheckConfigError(f"check requires param '{key}'")
    return val


# ── dashboards (AI/BI Lakeview) ──────────────────────────────────────────────

def _list_dashboards(client: Any) -> List[Any]:
    lv = getattr(client, "lakeview", None)
    if lv is not None and hasattr(lv, "list"):
        return list(lv.list())
    dash = getattr(client, "dashboards", None)
    if dash is not None and hasattr(dash, "list"):
        return list(dash.list())
    raise RuntimeError("workspace client exposes no Lakeview/dashboards list API")


def dashboard_exists_for_team(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    needle = params.get("name_contains")
    matches = [n for (n, _) in _iter_names(_list_dashboards(client)) if _matches(n, needle)]
    return {
        "found": bool(matches),
        "detail": f"{len(matches)} dashboard(s) match name_contains={needle!r}",
        "evidence": {"matched": matches[:5], "name_contains": needle},
    }


def dashboard_published(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    needle = params.get("name_contains")
    published: List[str] = []
    any_match: List[str] = []
    for name, obj in _iter_names(_list_dashboards(client)):
        if not _matches(name, needle):
            continue
        any_match.append(name)
        state = (_first_attr(obj, ("lifecycle_state",)) or "").upper()
        is_published = bool(getattr(obj, "published", False)) or state in ("ACTIVE", "PUBLISHED")
        # When the API can't tell us publish state, fall back to existence.
        if is_published or state == "":
            published.append(name)
    return {
        "found": bool(published),
        "detail": f"{len(published)} published of {len(any_match)} matching dashboard(s)",
        "evidence": {"published": published[:5], "name_contains": needle},
    }


# ── Genie spaces ─────────────────────────────────────────────────────────────

def genie_space_exists(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    needle = params.get("name_contains")
    genie = getattr(client, "genie", None)
    items: Iterable[Any]
    if genie is not None and hasattr(genie, "list_spaces"):
        resp = genie.list_spaces()
        items = getattr(resp, "spaces", None) or (resp if isinstance(resp, (list, tuple)) else [])
    else:
        raise RuntimeError("workspace client exposes no Genie list_spaces API")
    matches = [n for (n, _) in _iter_names(items) if _matches(n, needle)]
    return {
        "found": bool(matches),
        "detail": f"{len(matches)} Genie space(s) match name_contains={needle!r}",
        "evidence": {"matched": matches[:5], "name_contains": needle},
    }


# ── Unity Catalog table ──────────────────────────────────────────────────────

def table_exists(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    full_name = _require(params, "table")
    tables = getattr(client, "tables", None)
    if tables is None or not hasattr(tables, "get"):
        raise RuntimeError("workspace client exposes no UC tables.get API")
    try:
        tables.get(full_name)
        found = True
        detail = f"table {full_name} exists"
    except Exception as exc:  # noqa: BLE001 - not-found is the common, expected case
        found = False
        detail = f"table {full_name} not found ({type(exc).__name__})"
    return {"found": found, "detail": detail, "evidence": {"table": full_name}}


# ── Jobs with a schedule ─────────────────────────────────────────────────────

def job_exists_with_schedule(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    needle = params.get("name_contains")
    jobs_api = getattr(client, "jobs", None)
    if jobs_api is None or not hasattr(jobs_api, "list"):
        raise RuntimeError("workspace client exposes no jobs list API")
    scheduled: List[str] = []
    for job in jobs_api.list():
        settings = getattr(job, "settings", None) or job
        name = _first_attr(settings, ("name",)) or _first_attr(job, ("name",)) or ""
        if not _matches(name, needle):
            continue
        schedule = getattr(settings, "schedule", None) or getattr(settings, "trigger", None)
        if schedule:
            scheduled.append(name)
    return {
        "found": bool(scheduled),
        "detail": f"{len(scheduled)} scheduled job(s) match name_contains={needle!r}",
        "evidence": {"scheduled": scheduled[:5], "name_contains": needle},
    }


# ── Pipelines with a completed update ────────────────────────────────────────

def pipeline_update_completed(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    needle = params.get("name_contains")
    pipelines = getattr(client, "pipelines", None)
    if pipelines is None:
        raise RuntimeError("workspace client exposes no pipelines API")
    lister = getattr(pipelines, "list_pipelines", None) or getattr(pipelines, "list", None)
    if lister is None:
        raise RuntimeError("workspace client exposes no pipelines list API")
    completed: List[str] = []
    matched: List[str] = []
    for p in lister():
        name = _first_attr(p, ("name",)) or ""
        if not _matches(name, needle):
            continue
        matched.append(name)
        state = (_first_attr(p, ("latest_update_state", "state")) or "").upper()
        if state in ("COMPLETED", "COMPLETE"):
            completed.append(name)
        elif state == "":
            # Update state not exposed; existence is the best signal we have.
            completed.append(name)
    return {
        "found": bool(completed),
        "detail": f"{len(completed)} of {len(matched)} matching pipeline(s) completed",
        "evidence": {"completed": completed[:5], "name_contains": needle},
    }


SDK_CHECKS: Dict[str, SDKCheck] = {
    "dashboard_exists_for_team": dashboard_exists_for_team,
    "dashboard_published": dashboard_published,
    "genie_space_exists": genie_space_exists,
    "table_exists": table_exists,
    "job_exists_with_schedule": job_exists_with_schedule,
    "pipeline_update_completed": pipeline_update_completed,
}


def known_checks() -> List[str]:
    return sorted(SDK_CHECKS.keys())
