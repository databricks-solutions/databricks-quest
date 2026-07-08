"""Federation service (ADR_006) — the thin glue for multi-workspace mode.

Centralizes the few behaviours a child needs and the helpers the master uses,
so the rest of the app stays role-agnostic:

- ``deterministic_idempotency_key`` — the canonical scoring idempotency key so
  retries (or two children) never double-award.
- ``stamp_federated_write`` — attach (workspace_id, submitted_by) to write
  payloads when running as a child; a no-op in standalone/master.
- ``resolve_event_id`` — turn the configured event slug into an event_id.
- ``startup_checkin`` — one-shot ``event_workspaces`` upsert on child boot.

Everything is best-effort and import-safe: nothing here should ever take down
request handling or app startup.
"""

import logging
from typing import Any, Dict, Optional

import config
from repositories import EventsRepository, FederationRepository

logger = logging.getLogger("databricks-quest.services.federation")

_events = EventsRepository()
_federation = FederationRepository()


def deterministic_idempotency_key(
    workspace_id: Optional[str],
    event_id: str,
    task_id: str,
    scoring_rule: str = "base",
) -> str:
    """Canonical scoring idempotency key.

    ``{workspace_id}:{event_id}:{task_id}:{scoring_rule}`` — the existing UNIQUE
    constraint on ``scoring_events.idempotency_key`` then enforces single-award
    even if a child retries or two writers race. In standalone mode
    ``workspace_id`` is empty, which keeps today's keyspace stable.
    """
    ws = workspace_id or "local"
    return f"{ws}:{event_id}:{task_id}:{scoring_rule}"


def stamp_federated_write(payload: Dict[str, Any], submitted_by: Optional[str] = None) -> Dict[str, Any]:
    """Add federation stamps to a write payload when running as a child.

    Returns the same dict (mutated) for convenience. No-op for
    standalone/master, so callers can call it unconditionally.
    """
    if config.is_child() and config.QUEST_WORKSPACE_ID:
        payload.setdefault("workspace_id", config.QUEST_WORKSPACE_ID)
        if submitted_by is not None:
            payload.setdefault("submitted_by", submitted_by)
    return payload


def resolve_event_id(event_slug: Optional[str] = None) -> Optional[str]:
    """Resolve an event slug (default: the configured one) to its event_id."""
    slug = event_slug or config.QUEST_EVENT_SLUG
    if not slug:
        return None
    ev = _events.get_event_by_slug(slug)
    return ev["event_id"] if ev else None


def startup_checkin() -> bool:
    """Child-only: record this workspace's presence in the shared DB on boot.

    A plain row upsert (not an HTTP call). Best-effort: a failure here must not
    stop the app from serving, so we log and continue.
    """
    if not config.is_child():
        return False
    if not config.QUEST_WORKSPACE_ID:
        logger.warning("child role without QUEST_WORKSPACE_ID — skipping check-in")
        return False
    event_id = resolve_event_id()
    ok = _federation.checkin_workspace(
        workspace_id=config.QUEST_WORKSPACE_ID,
        event_id=event_id,
        event_slug=config.QUEST_EVENT_SLUG or None,
        app_url=config.QUEST_APP_URL or None,
        app_version=config.QUEST_APP_VERSION or None,
    )
    if ok:
        logger.info(
            "Workspace check-in recorded: workspace_id=%s event=%s",
            config.QUEST_WORKSPACE_ID,
            config.QUEST_EVENT_SLUG or event_id or "(unresolved)",
        )
    return ok


def child_status(submitted_by: Optional[str] = None) -> Dict[str, Any]:
    """Resolve the child's own team mapping + event for the UI ('your rank').

    Returns ``mapped: False`` with a friendly reason when the workspace/labuser
    is not yet on the roster, so the UI can show a 'not yet on a team' state.
    """
    out: Dict[str, Any] = {
        "role": config.QUEST_ROLE,
        "workspace_id": config.QUEST_WORKSPACE_ID or None,
        "event_slug": config.QUEST_EVENT_SLUG or None,
        "mapped": False,
        "team": None,
    }
    event_id = resolve_event_id()
    out["event_id"] = event_id
    if not event_id or not config.QUEST_WORKSPACE_ID or not submitted_by:
        return out
    identity = _federation.resolve_identity(event_id, config.QUEST_WORKSPACE_ID, submitted_by)
    if identity and identity.get("team_id"):
        out["mapped"] = True
        out["team"] = {
            "team_id": identity["team_id"],
            "team_name": identity.get("team_name"),
            "display_name": identity.get("team_display_name") or identity.get("display_name"),
        }
    return out
