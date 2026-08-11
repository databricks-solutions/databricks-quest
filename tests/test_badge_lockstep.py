"""The badge catalog is declared in three places that nothing keeps in sync.

``app/main.py`` (BADGE_DEFINITIONS) drives the "how to earn" checklist,
``notebooks/scoring_pipeline.py`` actually awards the rows, and
``frontend/src/lib/badges.ts`` renders the vault. ``badge_id`` is the only join
key between them: a plain string, no shared schema, no import. Nothing fails at
build or deploy time when one drifts, and each direction of drift fails silently
in a different way:

  - scoring awards an id the frontend lacks  -> badge is invisible in the vault
  - frontend lists an id scoring never grants -> permanently unearnable slot
  - criteria disagree with the award rule     -> the checklist completes but no
                                                 badge arrives, which reads as a
                                                 scoring bug

These tests pin the agreement so adding or renaming a badge has to touch all
three, and also check that every badge is reachable at all.
"""

import ast
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import main  # noqa: E402


SCORING = (ROOT / "notebooks" / "scoring_pipeline.py").read_text()
BADGES_TS = (ROOT / "frontend" / "src" / "lib" / "badges.ts").read_text()


def _backend():
    return {b["id"]: b for b in main.BADGE_DEFINITIONS}


def _scoring():
    """Parse MISSION_BADGES plus the two specially-awarded badges."""
    block = SCORING[SCORING.index("MISSION_BADGES = ["): SCORING.index("]\nfor b_id") + 1]
    out = {}
    for m in re.finditer(
        r'\("([a-z_]+)",\s*"([^"]+)",\s*"([^"]+)",\s*\n?\s*(\[[^\]]*\]),\s*(\d+)\)', block
    ):
        bid, name, _icon, missions, need = m.groups()
        out[bid] = {"name": name, "missions": ast.literal_eval(missions), "need": int(need)}
    # Awarded from their own subqueries rather than the mission-rule table.
    out["platform_explorer"] = {"name": "Platform Explorer", "special": True}
    out["databricks_legend"] = {"name": "Databricks Legend", "special": True}
    return out


def _frontend_ids():
    return list(dict.fromkeys(re.findall(r"id:\s*['\"]([a-z0-9_]+)['\"]", BADGES_TS)))


def test_same_badge_ids_in_all_three_layers():
    backend, scoring, frontend = set(_backend()), set(_scoring()), set(_frontend_ids())
    assert backend == scoring, f"backend vs scoring differ: {backend ^ scoring}"
    assert backend == frontend, f"backend vs frontend differ: {backend ^ frontend}"


def test_badge_names_match_between_backend_and_scoring():
    backend, scoring = _backend(), _scoring()
    mismatched = {
        bid: (backend[bid]["name"], scoring[bid]["name"])
        for bid in backend
        if backend[bid]["name"] != scoring[bid]["name"]
    }
    assert not mismatched, f"badge names differ: {mismatched}"


def test_award_rules_match_the_advertised_criteria():
    """The checklist a user sees must be the rule that actually grants the badge."""
    backend, scoring = _backend(), _scoring()
    for bid, b in backend.items():
        c = b["criteria"]
        if c["kind"] == "missions_all":
            need, pool = len(c["missions"]), c["missions"]
        elif c["kind"] == "missions_any":
            need, pool = 1, c["missions"]
        elif c["kind"] == "missions_count":
            need, pool = c["need"], c["missions"]
        else:
            assert scoring[bid].get("special"), f"{bid} has no mission rule in scoring"
            continue
        assert sorted(pool) == sorted(scoring[bid]["missions"]), f"{bid}: mission pool differs"
        assert need == scoring[bid]["need"], f"{bid}: threshold differs"


@pytest.mark.parametrize("badge", main.BADGE_DEFINITIONS, ids=lambda b: b["id"])
def test_no_unearnable_badges(badge):
    """Every mission a badge depends on has to exist, and the bar has to be reachable."""
    missions = {m["id"] for m in main.MISSION_DEFINITIONS}
    pool = badge["criteria"].get("missions", [])
    unknown = [m for m in pool if m not in missions]
    assert not unknown, f"{badge['id']} references missions that do not exist: {unknown}"
    if badge["criteria"]["kind"] == "missions_count":
        assert badge["criteria"]["need"] <= len(pool), f"{badge['id']} needs more than its pool"


def test_every_badge_has_artwork():
    art = {p.stem for p in (ROOT / "app" / "static" / "assets" / "badges").glob("*.png")}
    for bid in _backend():
        slug = bid.replace("_badge", "").replace("_", "-")
        assert slug in art, f"{bid}: missing {slug}.png"


def test_retired_badges_are_not_still_in_the_catalog():
    """Ids listed for cleanup must be genuinely gone, or the sweep deletes live badges."""
    block = re.search(r"RETIRED_BADGES = \(([^)]*)\)", SCORING).group(1)
    retired = set(re.findall(r'"([a-z_]+)"', block))
    assert retired, "RETIRED_BADGES should not be empty while old deployments exist"
    assert not (retired & set(_backend())), f"retired ids still in the catalog: {retired & set(_backend())}"


def test_legend_threshold_matches_the_number_of_other_badges():
    """Legend's 'collect every other badge' path must track the catalog size."""
    non_legend = [b for b in _backend() if b != "databricks_legend"]
    # scoring computes NON_LEGEND_BADGE_COUNT = 1 + len(MISSION_BADGES)
    scoring_count = 1 + len([b for b in _scoring().values() if not b.get("special")])
    assert scoring_count == len(non_legend), (
        f"Legend needs {scoring_count} badges but the catalog has {len(non_legend)} non-legend badges"
    )
