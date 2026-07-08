"""Writer-credential scope guard (ADR_006).

The shared event-writer role children use must be INSERT-only on the four
event-fact tables — a leaked child credential must never be able to mutate or
delete scores. This statically inspects the GRANT statements in deploy.sh so a
future edit that widens the grant fails CI loudly.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY_SH = os.path.join(REPO_ROOT, "deploy.sh")

FACT_TABLES = {"scoring_events", "task_attempts", "validation_results", "hints_taken"}


def _grant_lines():
    with open(DEPLOY_SH, "r", encoding="utf-8") as f:
        text = f.read()
    # The event-writer grants live inside provision_event_writer_role.
    return [ln.strip() for ln in text.splitlines() if "GRANT" in ln and "TO %I" in ln]


def test_fact_tables_are_insert_only():
    lines = _grant_lines()
    assert lines, "expected GRANT statements in deploy.sh"
    for ln in lines:
        grant = ln.upper()
        # Find which fact tables this grant touches.
        touched = {t for t in FACT_TABLES if t in ln}
        if not touched:
            continue
        # Extract the privilege list between GRANT and ON.
        m = re.search(r"GRANT\s+(.*?)\s+ON", grant)
        assert m, f"could not parse privileges from: {ln}"
        privs = {p.strip() for p in m.group(1).split(",")}
        # Fact tables may only ever be granted INSERT.
        assert privs == {"INSERT"}, f"fact tables must be INSERT-only, got {privs}: {ln}"


def test_no_destructive_or_blanket_grants_to_writer():
    lines = _grant_lines()
    joined = " ".join(lines).upper()
    assert "GRANT ALL" not in joined
    # No DELETE / TRUNCATE / DROP privilege anywhere in the writer grants.
    for forbidden in ("DELETE", "TRUNCATE", "DROP"):
        assert forbidden not in joined, f"writer role must not be granted {forbidden}"


def test_writer_can_read_leaderboard_views():
    """The writer needs SELECT on the leaderboard views for child visibility."""
    lines = " ".join(_grant_lines())
    assert "event_leaderboard" in lines
    assert "SELECT" in lines.upper()
