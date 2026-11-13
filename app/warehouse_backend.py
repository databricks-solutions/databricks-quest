"""Read-only data backend that serves the adoption app from Unity Catalog Delta
tables via a serverless SQL warehouse, as an alternative to Lakebase.

Selected with ``QUEST_DATA_BACKEND=warehouse``. The scoring pipeline already
writes the scored tables (mission_completions, leaderboard, user_profile_snapshot,
badges, ...) to ``{QUEST_CATALOG}.{QUEST_SCHEMA}`` in Delta; this module reads
them directly through the warehouse, so no Lakebase / sync step is needed.

The existing repo SQL uses psycopg2 ``%s`` placeholders and bare table names.
We translate ``%s`` -> Databricks SQL named markers (``:p0``...), set the
warehouse session catalog/schema so bare names resolve, and coerce result
values back to native types using the response manifest (the Statements API
returns every value as a string). Isolated + lazily imports the SDK so importing
``db`` never pulls in the Databricks SDK for the Lakebase path.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Tuple

logger = logging.getLogger("databricks-quest.warehouse")

_WAREHOUSE_ID = lambda: os.getenv("QUEST_SQL_WAREHOUSE_ID", "").strip()
_CATALOG = lambda: os.getenv("QUEST_CATALOG", "").strip()
_SCHEMA = lambda: (os.getenv("QUEST_SCHEMA", "quest").strip() or "quest")

_INT_TYPES = {"INT", "INTEGER", "BIGINT", "LONG", "SHORT", "BYTE", "SMALLINT", "TINYINT"}
_FLOAT_TYPES = {"FLOAT", "DOUBLE", "DECIMAL", "REAL"}


def _serialize(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _coerce(value, type_name: str):
    """Cast a Statements-API string cell to a native type per the column type."""
    if value is None:
        return None
    t = (type_name or "").upper()
    try:
        if t in _INT_TYPES:
            return int(value)
        if t in _FLOAT_TYPES:
            return float(value)
        if t == "BOOLEAN":
            return str(value).lower() == "true"
    except (ValueError, TypeError):
        return value
    return value  # STRING / TIMESTAMP / DATE / etc. stay as-is (already strings)


def _param(name: str, value: Any):
    from databricks.sdk.service.sql import StatementParameterListItem

    if value is None:
        return StatementParameterListItem(name=name, value=None, type="STRING")
    if isinstance(value, bool):
        return StatementParameterListItem(name=name, value=("true" if value else "false"), type="BOOLEAN")
    if isinstance(value, int):
        return StatementParameterListItem(name=name, value=str(value), type="INT")
    if isinstance(value, float):
        return StatementParameterListItem(name=name, value=repr(value), type="DOUBLE")
    return StatementParameterListItem(name=name, value=str(value), type="STRING")


def _translate(query: str, params: Tuple) -> Tuple[str, list]:
    """psycopg2 ``%s`` positional placeholders -> Databricks ``:pN`` named params."""
    if not params:
        return query, []
    parts = query.split("%s")
    if len(parts) - 1 != len(params):
        raise RuntimeError(
            f"warehouse query placeholder/param mismatch ({len(parts) - 1} vs {len(params)})"
        )
    out = parts[0]
    sdk_params = []
    for i, p in enumerate(params):
        out += f":p{i}" + parts[i + 1]
        sdk_params.append(_param(f"p{i}", p))
    return out, sdk_params


def query(sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    """Run a read query on the warehouse against the scored Delta tables."""
    return list(iter_query(sql, params))


def iter_query(sql: str, params: Tuple = ()) -> Iterator[Dict[str, Any]]:
    """Read every Statements API result chunk; reject incomplete results."""
    wid = _WAREHOUSE_ID()
    if not wid:
        raise RuntimeError("QUEST_SQL_WAREHOUSE_ID not set for warehouse data backend")
    from databricks.sdk import WorkspaceClient

    statement, sdk_params = _translate(sql, params)
    w = WorkspaceClient()
    resp = w.statement_execution.execute_statement(
        warehouse_id=wid,
        statement=statement,
        catalog=_CATALOG() or None,
        schema=_SCHEMA() or None,
        parameters=sdk_params or None,
        wait_timeout="50s",
    )

    def _state(r):
        return r.status.state.value if r.status and r.status.state else None

    deadline = time.monotonic() + 180
    state = _state(resp)
    while state in ("PENDING", "RUNNING"):
        if time.monotonic() >= deadline:
            w.statement_execution.cancel_execution(resp.statement_id)
            raise TimeoutError("warehouse query timed out")
        time.sleep(1)
        resp = w.statement_execution.get_statement(resp.statement_id)
        state = _state(resp)

    if state != "SUCCEEDED":
        err = resp.status.error.message if (resp.status and resp.status.error) else state
        raise RuntimeError(f"warehouse query failed: {err}")

    # Known mutation statements may legitimately have no result. Read statements
    # (including WITH, SHOW and commented SQL) must prove result completeness.
    verb = statement.lstrip().split(maxsplit=1)[0].upper()
    returns_rows = verb not in {"MERGE", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "GRANT", "REVOKE", "TRUNCATE", "USE", "SET"}
    if returns_rows and resp.manifest is None:
        raise RuntimeError("warehouse result missing manifest")
    expected_rows = resp.manifest.total_row_count if resp.manifest else None
    if returns_rows and (type(expected_rows) is not int or expected_rows < 0):
        raise RuntimeError("warehouse result missing or invalid row count metadata")
    schema = resp.manifest.schema if (resp.manifest and resp.manifest.schema) else None
    cols = [(c.name, getattr(c.type_name, "value", str(c.type_name))) for c in (schema.columns or [])] if schema else []
    if returns_rows and not cols:
        raise RuntimeError("warehouse result missing schema")
    names = [name for name, _ in cols]
    if any(not isinstance(name, str) or not name for name in names) or len(names) != len(set(names)):
        raise RuntimeError("warehouse result invalid column names")
    if resp.manifest and resp.manifest.truncated:
        raise RuntimeError("warehouse result truncated; use a smaller export page")
    chunk = resp.result
    row_count = 0
    seen_chunks = set()
    requested_index = 0
    while chunk is not None:
        rows = chunk.data_array or []
        if returns_rows and chunk.next_chunk_internal_link and chunk.next_chunk_index is None:
            raise RuntimeError("warehouse result missing next chunk index")
        # The API can return result={} for an empty SELECT. The manifest still
        # supplies schema and total_row_count=0, so no chunk metadata is needed.
        proven_empty = (
            expected_rows == 0 and not rows and requested_index == 0
            and chunk.next_chunk_index is None and chunk.next_chunk_internal_link is None
            and all(value in (None, 0) for value in (chunk.chunk_index, chunk.row_offset, chunk.row_count))
        )
        if returns_rows and not proven_empty and (
            chunk.chunk_index != requested_index
            or chunk.row_offset != row_count
            or chunk.row_count != len(rows)
        ):
            raise RuntimeError("warehouse result chunk metadata mismatch")
        for row in rows:
            if not cols:
                raise RuntimeError("warehouse result missing schema")
            if len(row) != len(cols):
                raise RuntimeError("warehouse result column count mismatch")
            row_count += 1
            yield {name: _serialize(_coerce(row[i], kind)) for i, (name, kind) in enumerate(cols)}
        next_index = chunk.next_chunk_index
        if next_index is None:
            break
        if next_index in seen_chunks:
            raise RuntimeError("warehouse result repeated a chunk")
        if type(next_index) is not int or next_index != requested_index + 1:
            raise RuntimeError("warehouse result invalid next chunk index")
        seen_chunks.add(next_index)
        requested_index = next_index
        chunk = w.statement_execution.get_statement_result_chunk_n(resp.statement_id, next_index)
    if expected_rows is not None and row_count != expected_rows:
        raise RuntimeError("warehouse result row count mismatch")
