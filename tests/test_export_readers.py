"""Exercise real export read helpers without Databricks credentials or Postgres."""

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import db


@pytest.fixture
def warehouse(monkeypatch):
    # Existing backend-toggle tests install their own module in sys.modules.
    spec = importlib.util.spec_from_file_location("export_warehouse_reader", Path(__file__).parents[1] / "app/warehouse_backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_WAREHOUSE_ID", lambda: "dev-warehouse")
    client = MagicMock()
    import databricks.sdk
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda: client)
    return module, client.statement_execution


def response(rows, next_index=None, count=None, truncated=False):
    from databricks.sdk.service.sql import (
        ColumnInfo, ColumnInfoTypeName, ResultData, ResultManifest, ResultSchema,
        StatementResponse, StatementState, StatementStatus,
    )
    return StatementResponse(
        statement_id="stmt-1", status=StatementStatus(state=StatementState.SUCCEEDED),
        manifest=ResultManifest(
            schema=ResultSchema(columns=[ColumnInfo(name="points", type_name=ColumnInfoTypeName.INT)]),
            truncated=truncated, total_row_count=len(rows) if count is None else count,
        ),
        result=ResultData(data_array=rows, next_chunk_index=next_index, chunk_index=0, row_offset=0, row_count=len(rows)),
    )


def test_warehouse_reads_all_chunks_and_coerces_numeric_cells(warehouse):
    module, api = warehouse
    from databricks.sdk.service.sql import ResultData

    api.execute_statement.return_value = response([["10"]], next_index=1, count=3)
    api.get_statement_result_chunk_n.side_effect = [
        ResultData(data_array=[["20"]], next_chunk_index=2, chunk_index=1, row_offset=1, row_count=1),
        ResultData(data_array=[[None]], chunk_index=2, row_offset=2, row_count=1),
    ]
    assert module.query("SELECT points FROM user_points_fact") == [{"points": 10}, {"points": 20}, {"points": None}]
    assert api.get_statement_result_chunk_n.call_args_list[0].args == ("stmt-1", 1)
    assert api.get_statement_result_chunk_n.call_count == 2


def test_warehouse_rejects_truncated_or_missing_rows(warehouse):
    module, api = warehouse
    api.execute_statement.return_value = response([["10"]], count=2, truncated=True)
    with pytest.raises(RuntimeError, match="truncated"):
        list(module.iter_query("SELECT points FROM user_points_fact"))
    api.execute_statement.return_value = response([["10"]], count=2)
    with pytest.raises(RuntimeError, match="row count mismatch"):
        list(module.iter_query("SELECT points FROM user_points_fact"))


def test_later_chunk_error_fails_export_reader(warehouse):
    module, api = warehouse
    api.execute_statement.return_value = response([["10"]], next_index=1, count=2)
    api.get_statement_result_chunk_n.side_effect = RuntimeError("lost chunk")
    with pytest.raises(RuntimeError, match="lost chunk"):
        list(module.iter_query("SELECT points FROM user_points_fact"))


def test_pending_warehouse_query_has_a_timeout(warehouse, monkeypatch):
    module, api = warehouse
    from databricks.sdk.service.sql import StatementResponse, StatementState, StatementStatus

    api.execute_statement.return_value = StatementResponse(
        statement_id="stmt-1", status=StatementStatus(state=StatementState.RUNNING),
    )
    times = iter([0, 181])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError):
        list(module.iter_query("SELECT points FROM user_points_fact"))
    api.cancel_execution.assert_called_once_with("stmt-1")


def test_lakebase_fetches_batches_and_serializes_values(monkeypatch):
    from datetime import date

    cursor = MagicMock()
    cursor.fetchmany.side_effect = [[{"user_id": "a", "completed_at": date(2026, 11, 13)}], [{"user_id": "b"}], []]
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    @contextmanager
    def lease(**kwargs):
        assert kwargs == {"autocommit": True}
        yield connection

    monkeypatch.setattr(db, "_lease", lease)
    assert list(db.iter_query("SELECT user_id FROM mission_completions", backend="lakebase")) == [
        {"user_id": "a", "completed_at": "2026-11-13"}, {"user_id": "b"},
    ]
    cursor.fetchall.assert_not_called()
    cursor.execute.assert_called_once_with("SELECT user_id FROM mission_completions", ())


def test_export_routing_uses_pinned_backend(monkeypatch):
    reader = MagicMock(return_value=iter([{"user_id": "a"}]))
    monkeypatch.setitem(sys.modules, "warehouse_backend", SimpleNamespace(iter_query=reader))
    monkeypatch.setattr(db, "get_data_backend", lambda: pytest.fail("Must not re-resolve active backend"))
    assert list(db.iter_query("SELECT user_id FROM badges", backend="warehouse")) == [{"user_id": "a"}]
    reader.assert_called_once_with("SELECT user_id FROM badges", ())


def test_repeated_chunk_link_fails_instead_of_looping(warehouse):
    module, api = warehouse
    from databricks.sdk.service.sql import ResultData

    api.execute_statement.return_value = response([["10"]], next_index=1, count=3)
    api.get_statement_result_chunk_n.return_value = ResultData(data_array=[["20"]], next_chunk_index=1, chunk_index=1, row_offset=1, row_count=1)
    with pytest.raises(RuntimeError, match="repeated a chunk"):
        module.query("SELECT points FROM user_points_fact")
    assert api.get_statement_result_chunk_n.call_count == 1


@pytest.mark.parametrize("state", ["FAILED", "CANCELED", "CLOSED"])
def test_unsuccessful_statement_never_looks_like_an_empty_export(warehouse, state):
    module, api = warehouse
    from databricks.sdk.service.sql import StatementResponse, StatementState, StatementStatus

    api.execute_statement.return_value = StatementResponse(
        statement_id="stmt-1", status=StatementStatus(state=StatementState(state)),
    )
    with pytest.raises(RuntimeError, match="warehouse query failed"):
        module.query("SELECT points FROM user_points_fact")


def test_pending_query_polls_and_preserves_pagination_parameter_types(warehouse, monkeypatch):
    module, api = warehouse
    from databricks.sdk.service.sql import StatementResponse, StatementState, StatementStatus

    api.execute_statement.return_value = StatementResponse(
        statement_id="stmt-1", status=StatementStatus(state=StatementState.PENDING),
    )
    api.get_statement.return_value = response([["17"]], count=1)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    assert module.query("SELECT points FROM user_points_fact LIMIT %s OFFSET %s", (10000, 20000)) == [{"points": 17}]
    args = api.execute_statement.call_args.kwargs
    assert args["statement"].endswith("LIMIT :p0 OFFSET :p1")
    assert [(p.name, p.value, p.type) for p in args["parameters"]] == [("p0", "10000", "INT"), ("p1", "20000", "INT")]


@pytest.mark.parametrize("malformed", ["missing_schema", "extra_cell", "missing_cell"])
def test_malformed_warehouse_rows_fail_instead_of_losing_values(warehouse, malformed):
    module, api = warehouse
    result = response([["10"]], count=1)
    if malformed == "missing_schema":
        result.manifest.schema = None
    else:
        result.result.data_array = [["10", "lost"]] if malformed == "extra_cell" else [[]]
    api.execute_statement.return_value = result
    with pytest.raises(RuntimeError, match="schema|column count"):
        module.query("SELECT points FROM user_points_fact")


@pytest.mark.parametrize("missing", ["manifest", "total_row_count", "positive_count_result"])
def test_select_requires_completeness_metadata_even_for_empty_results(warehouse, missing):
    module, api = warehouse
    result = response([], count=0)
    if missing == "manifest":
        result.manifest = None
        result.result = None
    elif missing == "total_row_count":
        result.manifest.total_row_count = None
    else:
        result.manifest.total_row_count = 1
        result.result = None
    api.execute_statement.return_value = result
    with pytest.raises(RuntimeError, match="manifest|metadata|row count"):
        module.query("SELECT points FROM user_points_fact")


@pytest.mark.parametrize("names", [["user_id", "user_id"], ["user_id", None]])
def test_select_rejects_duplicate_or_missing_column_names(warehouse, names):
    module, api = warehouse
    from databricks.sdk.service.sql import ColumnInfo, ColumnInfoTypeName
    result = response([["alice@example.test", "250"]], count=1)
    result.manifest.schema.columns = [ColumnInfo(name=name, type_name=ColumnInfoTypeName.STRING) for name in names]
    api.execute_statement.return_value = result
    with pytest.raises(RuntimeError, match="column names"):
        module.query("SELECT user_id, points FROM user_points_fact")


@pytest.mark.parametrize("invalid", ["replayed_chunk", "wrong_offset", "wrong_row_count"])
def test_select_rejects_inconsistent_chunk_identity_and_positions(warehouse, invalid):
    module, api = warehouse
    from databricks.sdk.service.sql import ResultData
    api.execute_statement.return_value = response([["10"]], next_index=1, count=2)
    api.get_statement_result_chunk_n.return_value = ResultData(
        data_array=[["20"]], chunk_index=0 if invalid == "replayed_chunk" else 1,
        row_offset=0 if invalid == "wrong_offset" else 1,
        row_count=2 if invalid == "wrong_row_count" else 1,
    )
    with pytest.raises(RuntimeError, match="chunk"):
        module.query("SELECT points FROM user_points_fact")


def test_non_select_without_results_remains_compatible(warehouse):
    module, api = warehouse
    result = response([])
    result.manifest = None
    result.result = None
    api.execute_statement.return_value = result
    assert module.query("MERGE INTO app_settings USING (SELECT 1) AS s ON false WHEN NOT MATCHED THEN INSERT *") == []


@pytest.mark.parametrize("envelope", [None, "empty"])
def test_empty_select_accepts_no_chunk_metadata_when_manifest_proves_zero_rows(warehouse, envelope):
    module, api = warehouse
    from databricks.sdk.service.sql import ResultData
    result = response([], count=0)
    result.result = ResultData() if envelope == "empty" else None
    api.execute_statement.return_value = result
    assert module.query("SELECT points FROM user_points_fact") == []


@pytest.mark.parametrize("with_metadata", [False, True])
def test_continuation_link_without_chunk_index_never_looks_complete(warehouse, with_metadata):
    module, api = warehouse
    from databricks.sdk.service.sql import ResultData
    result = response([], count=0)
    if not with_metadata:
        result.result = ResultData()
    result.result.next_chunk_internal_link = "/api/2.0/sql/statements/example/result/chunks/1"
    api.execute_statement.return_value = result
    with pytest.raises(RuntimeError, match="chunk"):
        module.query("SELECT points FROM user_points_fact")
