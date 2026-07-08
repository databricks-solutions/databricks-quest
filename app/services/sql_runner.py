"""Databricks SQL warehouse executor for the SQL assertion validator.

Isolated here (and imported lazily) so the validator logic and its tests never
depend on the Databricks SDK. The executor returns rows as column-ordered
dicts, matching what the validator's expectation evaluator expects.

The statement runs under the app's workspace identity (a scoped service
principal in a real event) against the configured warehouse. The validator has
already enforced read-only + single-statement + server-side templating before
anything reaches this layer.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("databricks-quest.services.sql_runner")

Executor = Callable[[str, int], List[Dict[str, Any]]]


def _default_warehouse_id() -> str:
    return os.getenv("QUEST_SQL_WAREHOUSE_ID", "").strip()


def build_warehouse_executor(warehouse_id: Optional[str] = None) -> Executor:
    """Return an executor bound to a warehouse via the SDK statement API.

    Raises ``RuntimeError`` at call time (not build time) if no warehouse is
    configured, so the validator surfaces it as an ``error`` outcome rather than
    failing at import.
    """
    wid = (warehouse_id or _default_warehouse_id()).strip()

    def _run(sql: str, timeout_seconds: int) -> List[Dict[str, Any]]:
        if not wid:
            raise RuntimeError(
                "no SQL warehouse configured (set QUEST_SQL_WAREHOUSE_ID or "
                "validator warehouse_id)"
            )
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.sql import (
            ExecuteStatementRequestOnWaitTimeout,
            StatementState,
        )

        # Clamp the wait to the API's 50s ceiling; the validator timeout still
        # governs the overall budget.
        wait = max(5, min(int(timeout_seconds or 30), 50))
        w = WorkspaceClient()
        resp = w.statement_execution.execute_statement(
            warehouse_id=wid,
            statement=sql,
            wait_timeout=f"{wait}s",
            # Must be the SDK enum (not the raw string) — the SDK reads ``.value``
            # when serializing the request.
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
        )
        state = resp.status.state if resp.status else None
        if state != StatementState.SUCCEEDED:
            detail = ""
            if resp.status and resp.status.error:
                detail = resp.status.error.message or ""
            raise RuntimeError(f"statement did not succeed (state={state}): {detail}")

        result = resp.result
        if not result or not result.data_array:
            return []
        columns = []
        if resp.manifest and resp.manifest.schema and resp.manifest.schema.columns:
            columns = [c.name for c in resp.manifest.schema.columns]
        rows: List[Dict[str, Any]] = []
        for raw in result.data_array:
            if columns:
                rows.append({columns[i]: raw[i] for i in range(min(len(columns), len(raw)))})
            else:
                rows.append({f"col_{i}": v for i, v in enumerate(raw)})
        return rows

    return _run
