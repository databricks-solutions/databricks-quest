"""Full Adoption Mode export: completeness, access control, and safe failures."""

import csv
import base64
import io
import json
import random
from datetime import datetime, timezone
from decimal import Decimal
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from services import adoption_export as export


def csv_rows(archive, filename):
    return list(csv.DictReader(io.StringIO(archive.read(filename).decode("utf-8-sig"))))


def build(read_rows=lambda *args, **kwargs: (), **kwargs):
    def complete_select_rows(sql, params=(), **options):
        # Minimal fixtures still model SELECT's full set of nullable columns.
        columns = sql.removeprefix("SELECT ").split(" FROM ")[0].split(", ")
        for row in read_rows(sql, params, **options):
            yield dict.fromkeys(columns) | row

    return export.build_export(
        complete_select_rows, backend=kwargs.get("backend", "lakebase"),
        missions=[{"id": "repeat", "name": "Repeat", "learning_resources": [{"url": "https://example.com"}]}],
        badges=[{"id": "starter", "criteria": {"kind": "metric", "need": 4}}],
        scoring_workspace_ids=["123"],
    )


@pytest.mark.parametrize("backend", ["warehouse", "lakebase"])
def test_full_export_preserves_users_awards_points_and_backend_training(backend):
    training = export.TRAINING[backend]
    data = {
        "user_profile_snapshot": [
            {"user_id": "a@corp.com", "total_points": 525, "display_name": "Zoë, 李"},
            {"user_id": "b@corp.com", "total_points": 0},
        ],
        "mission_completions": [
            {"user_id": "a@corp.com", "mission_id": "repeat", "points_awarded": 250, "period_start": "2026-11-02"},
            {"user_id": "a@corp.com", "mission_id": "repeat", "points_awarded": 250, "period_start": "2026-11-09"},
            {"user_id": "orphan@corp.com", "mission_id": "retired", "points_awarded": 10},
        ],
        "user_points_fact": [{"user_id": "a@corp.com", "event_type": "consumption", "points": 25}],
        "leaderboard": [{"user_id": f"user{i}@corp.com", "all_time_rank": i} for i in range(1, 23)],
        "badges": [{"user_id": "a@corp.com", "badge_id": "starter"}],
        "notifications": [{"user_id": "a@corp.com", "message": 'Congrats,\n"A"'}],
        training.table: [{"user_id": "a@corp.com", "course_id": "100"}],
    }
    calls = []

    def read(sql, params=(), **kwargs):
        calls.append((sql, params, kwargs["backend"]))
        table = sql.split(" FROM ")[1].split()[0]
        return iter(data[table])

    result = build(read, backend=backend)
    with ZipFile(result.file) as archive:
        assert len(archive.namelist()) == 11
        meta = json.loads(archive.read("metadata.json"))
        assert meta["data_backend"] == backend
        assert meta["scoring_workspace_ids"] == ["123"]
        assert meta["files"]["mission_completions.csv"]["row_count"] == 3
        assert csv_rows(archive, "user_profiles.csv")[0]["display_name"] == "Zoë, 李"
        awards = csv_rows(archive, "mission_completions.csv")
        assert [r["period_start"] for r in awards[:2]] == ["2026-11-02", "2026-11-09"]
        assert awards[2]["mission_id"] == "retired"  # no join may drop unknown missions/users
        assert csv_rows(archive, "points_history.csv")[0]["points"] == "25"
        assert len(csv_rows(archive, "leaderboard.csv")) == 22  # never the UI's top 10
        assert csv_rows(archive, "notifications.csv")[0]["message"] == 'Congrats,\n"A"'
        assert csv_rows(archive, training.filename)[0]["course_id"] == "100"
        assert json.loads(csv_rows(archive, "mission_catalog.csv")[0]["learning_resources"])[0]["url"] == "https://example.com"
        assert json.loads(csv_rows(archive, "badge_catalog.csv")[0]["criteria"])["need"] == 4
        for filename, info in meta["files"].items():
            assert len(csv_rows(archive, filename)) == info["row_count"]
        assert archive.read("user_profiles.csv").startswith(b"\xef\xbb\xbf")
    assert {call[2] for call in calls} == {backend}
    assert len(calls) == 7
    result.file.close()


def test_empty_datasets_still_have_headers_and_zero_counts():
    result = build()
    with ZipFile(result.file) as archive:
        assert csv_rows(archive, "user_profiles.csv") == []
        assert archive.read("user_profiles.csv").decode("utf-8-sig").startswith("user_id,display_name,total_points,")
        assert result.row_counts["user_profiles.csv"] == 0
        assert result.row_counts["mission_catalog.csv"] == 1
    result.file.close()


def test_warehouse_pages_until_exhausted_without_dropping_repeat_awards(monkeypatch):
    monkeypatch.setattr(export, "WAREHOUSE_PAGE_SIZE", 2)
    data = [{"user_id": "a", "mission_id": "repeat", "points_awarded": i} for i in range(6)]
    calls = []

    def read(sql, params=(), **kwargs):
        if "FROM mission_completions " not in sql:
            return []
        size, offset = params
        calls.append(offset)
        return data[offset:offset + size]

    result = build(read, backend="warehouse")
    with ZipFile(result.file) as archive:
        assert [r["points_awarded"] for r in csv_rows(archive, "mission_completions.csv")] == [str(i) for i in range(6)]
    assert calls == [0, 2, 4, 6]
    result.file.close()


@pytest.mark.parametrize("text", ["=HYPERLINK(1)", "+SUM(1)", "-cmd", "@SUM(1)", "  =1", "\ufeff=1", "\t=1", "\r=1", "\n=1"])
def test_csv_formula_cells_are_text(text):
    assert export._csv_value(text) == "'" + text


def test_numbers_null_dates_and_plain_text_keep_their_meaning():
    assert export._csv_value(-10) == -10
    assert export._csv_value(Decimal("12.30")) == "12.30"
    assert export._csv_value(None) == ""
    assert export._csv_value("hello") == "hello"
    assert export._csv_value(datetime(2026, 11, 13, tzinfo=timezone.utc)) == "2026-11-13T00:00:00+00:00"


def test_query_failure_closes_partial_archive(monkeypatch):
    files = []
    factory = export.SpooledTemporaryFile

    def temporary(**kwargs):
        file = factory(**kwargs)
        files.append(file)
        return file

    def broken(*args, **kwargs):
        yield {"user_id": "first"}
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(export, "SpooledTemporaryFile", temporary)
    with pytest.raises(RuntimeError, match="database unavailable"):
        build(broken)
    assert files[0].closed


def test_large_export_rolls_to_disk_and_streams_every_row():
    random_data = random.Random(20261113)

    def read(sql, params=(), **kwargs):
        if "FROM notifications " in sql:
            for index in range(12000):
                # Incompressible content crosses the real 8 MiB spool threshold.
                yield {"user_id": f"user{index}@example.test",
                       "message": base64.b64encode(random_data.randbytes(768)).decode("ascii")}

    result = build(read)
    assert result.size > 8 * 1024 * 1024
    assert result.file._rolled is True
    content = b"".join(result.chunks())
    assert result.file.closed
    assert len(content) == result.size
    with ZipFile(io.BytesIO(content)) as archive:
        assert archive.testzip() is None
        assert len(csv_rows(archive, "notifications.csv")) == 12000
        assert json.loads(archive.read("metadata.json"))["files"]["notifications.csv"]["row_count"] == 12000


def test_interrupted_download_closes_temporary_file():
    result = build()
    chunks = result.chunks()
    assert next(chunks).startswith(b"PK")
    chunks.close()
    assert result.file.closed


@pytest.mark.parametrize("extra", [False, True])
def test_dataset_rows_reject_missing_or_unexpected_fields(extra):
    dataset = export.DATASETS[0]
    row = dict.fromkeys(dataset.columns)
    if extra:
        row["unexpected"] = "must not silently disappear"
    else:
        del row["total_points"]
    with pytest.raises(RuntimeError, match="dataset columns"):
        list(export._dataset_rows(dataset, lambda *a, **kw: [row], "lakebase"))


@pytest.fixture
def client(monkeypatch):
    import main

    monkeypatch.setattr(main, "QUEST_ADMIN_ALLOWLIST", ["boss@corp.com"])
    monkeypatch.setattr(main.admins_repo, "list_emails", lambda: [])
    main._invalidate_admin_cache()
    monkeypatch.setattr(main.db, "get_data_backend", lambda: "lakebase")
    monkeypatch.setattr(main.db, "iter_query", lambda *a, **kw: iter(()))
    monkeypatch.setattr(main, "get_scoring_workspace_ids", lambda: ["123"])
    yield TestClient(main.app)
    main._invalidate_admin_cache()


@pytest.mark.parametrize("email", ["player@corp.com", ""])
def test_export_denies_non_admin_before_reading_data(client, monkeypatch, email):
    import main

    def unexpected(*a, **kw):
        pytest.fail("Unauthorized request must not build an export")

    monkeypatch.setattr(main.adoption_export, "build_export", unexpected)
    response = client.get("/api/admin/export", headers={"X-Forwarded-Email": email})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_export_fails_closed_with_no_admins_in_deployed_app(client, monkeypatch):
    import main

    monkeypatch.setattr(main, "QUEST_ADMIN_ALLOWLIST", [])
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "app-service-principal")
    response = client.get("/api/admin/export", headers={"X-Forwarded-Email": "boss@corp.com"})
    assert response.status_code == 403


def test_admin_download_headers_and_archive(client):
    response = client.get("/api/admin/export", headers={"X-Forwarded-Email": "boss@corp.com"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert 'attachment; filename="quest-adoption-export-' in response.headers["content-disposition"]
    assert int(response.headers["content-length"]) == len(response.content)
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert len(csv_rows(archive, "mission_catalog.csv")) == 60
        assert "README.txt" in archive.namelist()


def test_endpoint_returns_safe_error_instead_of_partial_zip(client, monkeypatch):
    import main

    def broken(*a, **kw):
        raise RuntimeError("secret database credentials")

    monkeypatch.setattr(main.db, "iter_query", broken)
    response = client.get("/api/admin/export", headers={"X-Forwarded-Email": "boss@corp.com"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EXPORT_FAILED"
    assert "secret database credentials" not in response.text
    assert "content-disposition" not in response.headers
