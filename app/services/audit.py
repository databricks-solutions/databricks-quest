"""Audit logging for GameDay mutations.

Every host/admin/player mutation should call :func:`record_audit` so there is a
durable trail in ``event_audit_log``. Auditing is best-effort: a failure to
write the audit row is logged but never raised, so it cannot take down the
operation it is recording. Callers that require a guaranteed audit trail should
write the audit row inside their own transaction instead.

Never store secrets, tokens, or raw credentials in ``payload_json`` — store a
short, safe summary only.
"""

import json
import logging
import uuid
from typing import Any, Dict, Optional

import db

logger = logging.getLogger("databricks-quest.services.audit")


def record_audit(
    action: str,
    actor_user_id: Optional[str] = None,
    event_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert an audit row and return its id (or None if the write failed).

    Args:
        action: Short verb describing what happened, e.g. ``"event.start"``.
        actor_user_id: Identity that performed the action.
        event_id: Event the action is scoped to, if any.
        target_type: Type of the affected object, e.g. ``"team"``.
        target_id: Id of the affected object.
        payload: Small, non-sensitive JSON summary of the change.
    """
    audit_id = f"aud_{uuid.uuid4().hex[:16]}"
    try:
        db.execute(
            """
            INSERT INTO event_audit_log
                (audit_id, event_id, actor_user_id, action,
                 target_type, target_id, payload_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                audit_id,
                event_id,
                actor_user_id,
                action,
                target_type,
                target_id,
                json.dumps(payload) if payload is not None else None,
            ),
        )
        return audit_id
    except Exception as exc:  # noqa: BLE001 - auditing must never break the caller
        logger.warning("record_audit failed for action=%s: %s", action, exc)
        return None
