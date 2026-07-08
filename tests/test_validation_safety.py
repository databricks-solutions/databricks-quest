"""SQL safety + server-side templating for the SQL assertion validator (PR03).

These guard the read-only / single-statement / no-injection posture. They are
pure-function tests (no DB), so they double as a security spec.
"""

import pytest

from validators.safety import (
    UnsafeSQLError,
    ensure_safe_select,
    prepare_statement,
    referenced_variables,
    resolve_template,
    split_statements,
)


# ── read-only enforcement ────────────────────────────────────────────────────


def test_plain_select_is_allowed():
    sql = "SELECT COUNT(*) AS cnt FROM main.demo.gold"
    assert ensure_safe_select(sql) == sql


def test_with_cte_select_is_allowed():
    sql = "WITH t AS (SELECT 1 AS x) SELECT x FROM t"
    assert ensure_safe_select(sql).startswith("WITH")


def test_trailing_semicolon_is_tolerated():
    assert ensure_safe_select("SELECT 1 ;").startswith("SELECT 1")


@pytest.mark.parametrize(
    "stmt",
    [
        "DROP TABLE main.demo.gold",
        "DELETE FROM main.demo.gold",
        "UPDATE main.demo.gold SET x = 1",
        "INSERT INTO main.demo.gold VALUES (1)",
        "TRUNCATE TABLE main.demo.gold",
        "ALTER TABLE main.demo.gold ADD COLUMN y INT",
        "CREATE TABLE main.demo.x (a INT)",
        "GRANT SELECT ON main.demo.gold TO `x`",
        "MERGE INTO a USING b ON a.id=b.id WHEN MATCHED THEN DELETE",
    ],
)
def test_destructive_statements_blocked(stmt):
    with pytest.raises(UnsafeSQLError):
        ensure_safe_select(stmt)


def test_select_with_embedded_blocked_verb_is_rejected():
    # A subquery dressed up as a SELECT but containing a blocked verb.
    with pytest.raises(UnsafeSQLError):
        ensure_safe_select("SELECT * FROM x; DROP TABLE y")


def test_multiple_statements_rejected():
    with pytest.raises(UnsafeSQLError):
        ensure_safe_select("SELECT 1; SELECT 2")


def test_empty_is_rejected():
    with pytest.raises(UnsafeSQLError):
        ensure_safe_select("   ")


# ── statement splitting (semicolon/quote/comment aware) ──────────────────────


def test_split_ignores_semicolons_in_strings():
    stmts = split_statements("SELECT ';' AS s")
    assert len(stmts) == 1


def test_split_ignores_semicolons_in_comments():
    stmts = split_statements("SELECT 1 -- a;b\n")
    assert len(stmts) == 1


def test_block_comment_cannot_hide_second_statement():
    # The block comment is stripped; the real ';DROP' becomes a 2nd statement.
    with pytest.raises(UnsafeSQLError):
        ensure_safe_select("SELECT 1 /* hi */ ; DROP TABLE x")


# ── server-side templating ───────────────────────────────────────────────────


def test_referenced_variables_are_discovered():
    sql = "SELECT * FROM ${team_catalog}.${team_schema}.gold"
    assert referenced_variables(sql) == ["team_catalog", "team_schema"]


def test_resolve_template_substitutes_known_variables():
    sql = "SELECT * FROM ${team_catalog}.${team_schema}.gold"
    resolved, applied = resolve_template(
        sql, {"team_catalog": "team_red", "team_schema": "gameday"}
    )
    assert resolved == "SELECT * FROM team_red.gameday.gold"
    assert applied == {"team_catalog": "team_red", "team_schema": "gameday"}


def test_unknown_template_variable_is_rejected():
    with pytest.raises(UnsafeSQLError):
        resolve_template("SELECT * FROM ${evil}", {"team_catalog": "x"})


def test_template_value_with_sql_metacharacters_is_rejected():
    with pytest.raises(UnsafeSQLError):
        resolve_template(
            "SELECT * FROM ${team_schema}.gold",
            {"team_schema": "x; DROP TABLE y"},
        )


def test_prepare_statement_resolves_then_enforces_safety():
    sql = "SELECT COUNT(*) FROM ${team_catalog}.${team_schema}.gold"
    out = prepare_statement(
        sql, {"team_catalog": "c", "team_schema": "s"}
    )
    assert out == "SELECT COUNT(*) FROM c.s.gold"


def test_prepare_statement_blocks_injection_via_template():
    # Even though the value is rejected first, confirm the safety net holds.
    with pytest.raises(UnsafeSQLError):
        prepare_statement(
            "SELECT * FROM ${schema}",
            {"schema": "a; DROP TABLE b"},
        )
