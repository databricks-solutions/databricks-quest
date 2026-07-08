"""Structured logging + request correlation for GameDay (PR10).

Two concerns, kept dependency-free:

1. **Request IDs** — every request gets a short correlation id (honouring an
   inbound ``X-Request-ID`` if present) that is echoed on the response and
   attached to error envelopes, so a player-reported failure can be tied to the
   exact server logs.
2. **Structured event logs** — validation and scoring emit one machine-parsable
   line each (``key=value`` pairs) so an operator can grep/ingest them without a
   tracing backend. Player-facing messages are never logged at info level with
   sensitive payloads; only ids, types, statuses, and point deltas.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger("databricks-quest.observability")

REQUEST_ID_HEADER = "X-Request-ID"


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"


def normalize_request_id(raw: Optional[str]) -> str:
    """Accept an inbound id (trimmed/clamped) or mint a new one.

    Clamps to 64 chars and strips anything outside a safe charset so a malicious
    header can't inject into logs.
    """
    if raw:
        cleaned = "".join(c for c in raw.strip() if c.isalnum() or c in "-_")[:64]
        if cleaned:
            return cleaned
    return new_request_id()


def _kv(fields: dict) -> str:
    parts = []
    for k, v in fields.items():
        if v is None:
            continue
        s = str(v).replace("\n", " ").replace('"', "'")
        if " " in s:
            s = f'"{s}"'
        parts.append(f"{k}={s}")
    return " ".join(parts)


def log_validation(
    *,
    request_id: Optional[str],
    event_id: Optional[str],
    task_id: Optional[str],
    team_id: Optional[str],
    validator_id: Optional[str],
    validator_type: Optional[str],
    status: str,
    score_delta: Any = None,
) -> None:
    """Emit one structured line per validator outcome (no player payloads)."""
    logger.info(
        "validation %s",
        _kv({
            "request_id": request_id,
            "event_id": event_id,
            "task_id": task_id,
            "team_id": team_id,
            "validator_id": validator_id,
            "type": validator_type,
            "status": status,
            "score_delta": score_delta,
        }),
    )


def log_scoring(
    *,
    request_id: Optional[str],
    event_id: Optional[str],
    team_id: Optional[str],
    workspace_id: Optional[str],
    source_type: str,
    points_delta: Any,
    awarded: bool,
    reason: Optional[str] = None,
) -> None:
    """Emit one structured line per scoring decision (awarded or idempotent skip)."""
    logger.info(
        "scoring %s",
        _kv({
            "request_id": request_id,
            "event_id": event_id,
            "team_id": team_id,
            "workspace_id": workspace_id,
            "source_type": source_type,
            "points_delta": points_delta,
            "awarded": awarded,
            "reason": reason,
        }),
    )
