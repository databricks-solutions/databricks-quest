"""Real SQL roundtrips in disposable local Postgres; never connects to Lakebase.

Requires initdb and pg_ctl on PATH. Other environments skip these integration
checks explicitly; the regular export tests do not require a database server.
"""

import csv
import io
import json
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pytest
import psycopg2

import db
from deploy import lakebase_ddl
from services import adoption_export as export


@pytest.fixture(scope="module")
def postgres():
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if os.name == "nt" or not initdb or not pg_ctl:
        pytest.skip("Local Postgres integration requires Unix, initdb, and pg_ctl")
    with TemporaryDirectory(prefix="quest-export-pg-") as directory:
        data = os.path.join(directory, "data")
        subprocess.run(
            [initdb, "-D", data, "--auth-local=trust", "--auth-host=reject", "--no-locale", "--encoding=UTF8"],
            check=True, capture_output=True,
        )
        subprocess.run(
            [pg_ctl, "-D", data, "-l", os.path.join(directory, "server.log"),
             "-o", f"-k {directory} -h '' -p 55473 -F", "-w", "start"],
            check=True, capture_output=True,
        )
        try:
            connection = psycopg2.connect(host=directory, port=55473, dbname="postgres")
            connection.autocommit = True
            try:
                with connection.cursor() as cursor:
                    cursor.execute(lakebase_ddl())
                    cursor.execute("CREATE TABLE training_completions (user_id TEXT, course_id TEXT, course_name TEXT, course_type TEXT, completed_at TIMESTAMP)")
                    # Cross the real 10,000-row warehouse page boundary twice,
                    # retain multiple awards per mission/user and an exact duplicate.
                    cursor.execute("""
                        INSERT INTO mission_completions
                        SELECT 'user_' || (g % 73)::text || '@example.test',
                               CASE WHEN g % 17 = 0 THEN 'retired' ELSE 'repeat' END,
                               'Misión, "数据"' || E'\n' || 'second line', (g % 400),
                               TIMESTAMP '2026-01-01' + g * INTERVAL '1 second',
                               DATE '2026-01-01' + (g / 73), NULL,
                               TIMESTAMP '2026-11-13'
                        FROM generate_series(1, 25001) AS g
                    """)
                    cursor.execute("INSERT INTO mission_completions SELECT * FROM mission_completions LIMIT 1")
                    cursor.execute("""
                        INSERT INTO user_profile_snapshot
                            (user_id, display_name, total_points, level, current_streak, updated_at)
                        VALUES ('user_1@example.test', 'Zoë, 李', 12345, 'Elite', 5, '2026-11-13'),
                               ('zero@example.test', 'Zero points', 0, 'Bronze', 0, '2026-11-13')
                    """)
                    cursor.execute("""
                        INSERT INTO user_points_fact (user_id, event_type, mission_id, points, reason, event_timestamp)
                        VALUES ('user_1@example.test', 'consumption', 'weekly_dbu', 25, 'DBU points', '2026-11-13'),
                               ('user_1@example.test', 'mission', 'repeat', -10, 'Adjustment', '2026-11-12')
                    """)
                    cursor.execute("""
                        INSERT INTO leaderboard (user_id, total_points, weekly_points, all_time_rank)
                        SELECT 'user_' || g::text || '@example.test', 1000-g, 100-g, g
                        FROM generate_series(1, 35) AS g
                    """)
                    cursor.execute("INSERT INTO badges (user_id, badge_id, badge_name, earned_at) VALUES ('user_1@example.test', 'starter', 'Starter', '2026-11-13')")
                    cursor.execute("INSERT INTO notifications (user_id, title, message, points) VALUES (%s, %s, %s, %s)",
                                   ('user_1@example.test', '=1+1', 'Unicode, 李\n"quoted"', -10))
                    cursor.execute("INSERT INTO training_attestations VALUES ('user_1@example.test', 'course', '100', '2026-11-13')")
                    cursor.execute("INSERT INTO training_completions VALUES ('user_1@example.test', '100', 'Course', 'classroom', '2026-11-13')")
                yield connection
            finally:
                connection.close()
        finally:
            subprocess.run([pg_ctl, "-D", data, "-m", "fast", "-w", "stop"], check=True, capture_output=True)


@pytest.fixture
def database_reader(postgres, monkeypatch):
    monkeypatch.setattr(db, "get_connection", lambda: postgres)
    monkeypatch.setattr(db, "_writer_credential_path", lambda: False)

    def read(sql, params=(), **kwargs):
        # Both backends use the exact generated SELECT/ORDER/LIMIT/OFFSET SQL.
        # Statements API chunk handling has separate SDK contract tests.
        yield from db.iter_query(sql, params, backend="lakebase")

    return read


def records(archive, filename):
    return list(csv.DictReader(io.StringIO(archive.read(filename).decode("utf-8-sig"))))


@pytest.mark.parametrize("backend", ["lakebase", "warehouse"])
def test_real_sql_full_export_and_retry_preserve_every_row(postgres, database_reader, backend):
    before = {}
    with postgres.cursor() as cursor:
        for dataset in (*export.DATASETS, export.TRAINING[backend]):
            cursor.execute(f"SELECT COUNT(*) FROM {dataset.table}")
            before[dataset.filename] = cursor.fetchone()[0]
    for _ in range(2):
        report = export.build_export(database_reader, backend=backend,
                                     missions=[{"id": "repeat", "name": "Repeat"}], badges=[], scoring_workspace_ids=[])
        try:
            with ZipFile(report.file) as archive:
                assert archive.testzip() is None
                metadata = json.loads(archive.read("metadata.json"))
                for filename, count in before.items():
                    assert len(records(archive, filename)) == count
                    assert metadata["files"][filename]["row_count"] == count
                completions = records(archive, "mission_completions.csv")
                assert len(completions) == 25002
                assert any(row["mission_id"] == "retired" for row in completions)
                assert completions[0]["mission_name"] == 'Misión, "数据"\nsecond line'
                assert len(records(archive, "leaderboard.csv")) == 35
                assert {row["total_points"] for row in records(archive, "user_profiles.csv")} == {"12345", "0"}
                notification = records(archive, "notifications.csv")[0]
                assert notification["title"] == "'=1+1"
                assert notification["points"] == "-10"
                assert notification["message"] == 'Unicode, 李\n"quoted"'
        finally:
            report.file.close()
    # Retries export data without changing any source rows.
    with postgres.cursor() as cursor:
        for dataset in (*export.DATASETS, export.TRAINING[backend]):
            cursor.execute(f"SELECT COUNT(*) FROM {dataset.table}")
            assert cursor.fetchone()[0] == before[dataset.filename]
    assert postgres.autocommit is True


def test_database_error_after_completed_datasets_closes_spool(database_reader, monkeypatch):
    files = []
    factory = export.SpooledTemporaryFile

    def spool(**kwargs):
        result = factory(**kwargs)
        files.append(result)
        return result

    def fail_late(sql, params=(), **kwargs):
        if "FROM notifications " in sql:
            yield from database_reader("SELECT * FROM missing_export_table")
        else:
            yield from database_reader(sql, params, **kwargs)

    monkeypatch.setattr(export, "SpooledTemporaryFile", spool)
    with pytest.raises(psycopg2.errors.UndefinedTable):
        export.build_export(fail_late, backend="lakebase", missions=[], badges=[], scoring_workspace_ids=[])
    assert files[0].closed


def test_real_database_endpoint_download(database_reader, monkeypatch):
    import main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "QUEST_ADMIN_ALLOWLIST", ["admin@example.test"])
    monkeypatch.setattr(main.admins_repo, "list_emails", lambda: [])
    monkeypatch.setattr(main.db, "get_data_backend", lambda: "lakebase")
    monkeypatch.setattr(main, "get_scoring_workspace_ids", lambda: ["123"])
    main._invalidate_admin_cache()
    try:
        response = TestClient(main.app).get("/api/admin/export", headers={"X-Forwarded-Email": "admin@example.test"})
        assert response.status_code == 200
        assert int(response.headers["content-length"]) == len(response.content)
        with ZipFile(io.BytesIO(response.content)) as archive:
            assert len(records(archive, "mission_completions.csv")) == 25002
            assert len(records(archive, "mission_catalog.csv")) == 60
    finally:
        main._invalidate_admin_cache()
