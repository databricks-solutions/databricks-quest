"""Local logic tests for the Get Started training missions (no Databricks needed).

Two things are covered without a Spark cluster:

1. **Definition integrity** — the mission entries in ``app/main.py`` and the
   ``GET_STARTED_COURSES`` mapping in ``notebooks/scoring_pipeline.py`` agree on
   mission ids, and every training mission is well-formed.

2. **Scoring semantics** — a pure-Python model of the two scoring blocks
   (per-course award + the Databricks Learner bonus), asserting the behaviours a
   real reviewer flagged as risky:
     - only instructor-led (``classroom``) completions score; self-paced never does
     - a NULL ``completed_at`` never produces a mission row (idempotency guard)
     - language-variant editions of the SAME course collapse to one distinct course
     - the Learner bonus fires at >= 2 DISTINCT courses, timestamped at the 2nd one

Run: ``python3 tests/test_get_started_training.py`` or ``pytest tests/test_get_started_training.py``
"""

import ast
import os
import re

REPO = os.path.join(os.path.dirname(__file__), "..")
MAIN_PY = os.path.join(REPO, "app", "main.py")
SCORING_PY = os.path.join(REPO, "notebooks", "scoring_pipeline.py")

GS_MISSION_IDS = [
    "gs_data_engineering", "gs_machine_learning", "gs_generative_ai",
    "gs_sql_analytics_bi", "gs_data_warehousing", "gs_platform_admin",
    "gs_data_governance", "gs_lakebase", "gs_lakehouse_architecture", "gs_ai_agents",
]
LEARNER_ID = "databricks_learner"
GET_STARTED_POINTS = 250
LEARNER_BONUS_POINTS = 500
LEARNER_THRESHOLD = 2


# --------------------------------------------------------------------------- #
# Extractors — read the real source without importing (main.py needs the SDK). #
# --------------------------------------------------------------------------- #
def _main_getting_started_ids():
    """mission ids in main.py whose category is 'Getting Started'."""
    src = open(MAIN_PY).read()
    ids = []
    for line in src.splitlines():
        line = line.strip()
        if line.startswith('{"id": "') and '"category": "Getting Started"' in line:
            ids.append(re.search(r'"id": "([^"]+)"', line).group(1))
    return ids


def _scoring_course_ids():
    """{mission_id: [course_id, ...]} parsed from GET_STARTED_COURSES literal."""
    src = open(SCORING_PY).read()
    open_pos = src.index("[", src.index("GET_STARTED_COURSES = ["))
    # walk to the matching close bracket (the list contains nested [...] lists)
    depth = 0
    for i in range(open_pos, len(src)):
        if src[i] == "[":
            depth += 1
        elif src[i] == "]":
            depth -= 1
            if depth == 0:
                close_pos = i
                break
    courses = ast.literal_eval(src[open_pos:close_pos + 1])
    return {c["mission_id"]: c["course_ids"] for c in courses}


# --------------------------------------------------------------------------- #
# Pure-Python model of the two scoring blocks.                                #
# --------------------------------------------------------------------------- #
def _course_of(course_id, course_ids_map):
    for mission_id, ids in course_ids_map.items():
        if course_id in ids:
            return mission_id
    return None


def score(rows, course_ids_map, threshold=LEARNER_THRESHOLD,
          accepted_types=("classroom", "self_attested")):
    """Mirror scoring_pipeline.py Step 2b. `rows`: (user, course_id, type, date|None).

    Returns {user: {mission_id: (points, completed_at)}}.
    """
    # inner: per (user, course) MIN(completed_at) over accepted, non-null rows
    firsts = {}  # (user, mission_id) -> earliest date
    for user, cid, ctype, dt in rows:
        if ctype not in accepted_types or dt is None:      # _TC_CLASSROOM filter
            continue
        m = _course_of(cid, course_ids_map)
        if m is None:                                # unknown course id ignored
            continue
        k = (user, m)
        if k not in firsts or dt < firsts[k]:
            firsts[k] = dt

    out = {}
    per_user_dates = {}
    for (user, mission_id), dt in firsts.items():
        out.setdefault(user, {})[mission_id] = (GET_STARTED_POINTS, dt)
        per_user_dates.setdefault(user, []).append(dt)

    # Databricks Learner: >= threshold DISTINCT courses; ts = the threshold-th date
    for user, dates in per_user_dates.items():
        if len(dates) >= threshold:
            nth = sorted(dates)[threshold - 1]
            out[user][LEARNER_ID] = (LEARNER_BONUS_POINTS, nth)
    return out


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #
def test_main_defines_all_training_missions():
    ids = _main_getting_started_ids()
    for mid in GS_MISSION_IDS + [LEARNER_ID]:
        assert mid in ids, f"{mid} missing from Getting Started in main.py"


def test_mission_id_parity_between_main_and_scoring():
    scoring = _scoring_course_ids()
    assert sorted(scoring.keys()) == sorted(GS_MISSION_IDS), "scoring course ids drift from expected set"
    main_ids = set(_main_getting_started_ids())
    for mid in scoring:
        assert mid in main_ids, f"scoring mission {mid} has no main.py definition"


def test_course_id_sets_are_disjoint():
    """No Academy course id maps to two different missions."""
    scoring = _scoring_course_ids()
    seen = {}
    for mid, ids in scoring.items():
        for cid in ids:
            assert cid not in seen, f"course {cid} in both {seen.get(cid)} and {mid}"
            seen[cid] = mid


def test_self_paced_never_scores():
    m = _scoring_course_ids()
    de = m["gs_data_engineering"][0]
    result = score([("u@x.com", de, "elearning", "2026-01-01")], m)
    assert result == {}, "self-paced (elearning) completion must not score"


def test_null_timestamp_never_produces_a_row():
    m = _scoring_course_ids()
    de = m["gs_data_engineering"][0]
    result = score([("u@x.com", de, "classroom", None)], m)
    assert result == {}, "NULL completed_at must be excluded (idempotency guard)"


def test_single_course_awards_no_learner_bonus():
    m = _scoring_course_ids()
    de = m["gs_data_engineering"][0]
    r = score([("u@x.com", de, "classroom", "2026-01-01")], m)
    assert r["u@x.com"].get("gs_data_engineering") == (250, "2026-01-01")
    assert LEARNER_ID not in r["u@x.com"], "1 course must not earn the Learner bonus"


def test_language_variants_collapse_to_one_course():
    m = _scoring_course_ids()
    de_ids = m["gs_data_engineering"]
    assert len(de_ids) >= 2, "DE should have language-variant ids to exercise this"
    english, variant = de_ids[0], de_ids[1]
    r = score([
        ("u@x.com", english, "classroom", "2026-01-01"),
        ("u@x.com", variant, "classroom", "2026-02-01"),
    ], m)
    assert r["u@x.com"]["gs_data_engineering"] == (250, "2026-01-01"), "same course scores once, earliest date"
    assert LEARNER_ID not in r["u@x.com"], "two editions of ONE course is not 2 distinct courses"


def test_learner_bonus_fires_at_two_distinct_courses():
    m = _scoring_course_ids()
    de = m["gs_data_engineering"][0]
    ml = m["gs_machine_learning"][0]
    r = score([
        ("u@x.com", de, "classroom", "2026-01-10"),
        ("u@x.com", ml, "classroom", "2026-02-15"),
    ], m)
    assert r["u@x.com"]["gs_data_engineering"] == (250, "2026-01-10")
    assert r["u@x.com"]["gs_machine_learning"] == (250, "2026-02-15")
    assert r["u@x.com"][LEARNER_ID] == (500, "2026-02-15"), "bonus timestamped at the 2nd distinct course"


def test_learner_bonus_timestamp_is_second_in_time_not_last_completed():
    """If courses are completed out of order, the bonus fires when the 2nd distinct
    course (chronologically) was done — here the ML date, done after DE."""
    m = _scoring_course_ids()
    de = m["gs_data_engineering"][0]
    ml = m["gs_machine_learning"][0]
    r = score([
        ("u@x.com", ml, "classroom", "2026-04-01"),   # entered first
        ("u@x.com", de, "classroom", "2026-05-01"),   # 2nd distinct in time
    ], m)
    assert r["u@x.com"][LEARNER_ID] == (500, "2026-05-01")


# --- tick-box / free-self-paced additions ---------------------------------- #
# The free self-paced course id each mission's tick attests against == the first
# path segment of the mission's doc_url (…/courses/<id>/…) in main.py. That id must
# be in the mission's scoring course_ids set, or a tick would never reconcile.
FREE_SELF_PACED = {
    "gs_data_engineering": "2469", "gs_machine_learning": "2460",
    "gs_generative_ai": "2724", "gs_sql_analytics_bi": "3347",
    "gs_data_warehousing": "3603", "gs_platform_admin": "2453",
    "gs_data_governance": "4677", "gs_lakebase": "5081",
    "gs_lakehouse_architecture": "3509", "gs_ai_agents": "4459",
}


def test_main_doc_url_free_course_id_matches_mission():
    """Each mission's doc_url course id is the expected FREE self-paced id."""
    src = open(MAIN_PY).read()
    for line in src.splitlines():
        line = line.strip()
        if not (line.startswith('{"id": "gs_') or line.startswith('{"id": "databricks_learner"')):
            continue
        mid = re.search(r'"id": "([^"]+)"', line).group(1)
        if mid == "databricks_learner":
            continue
        doc = re.search(r'"doc_url": f"\{_ACAD\}/(\d+)/', line)
        assert doc, f"{mid}: doc_url not an _ACAD course link"
        assert doc.group(1) == FREE_SELF_PACED[mid], f"{mid}: doc_url id {doc.group(1)} != free id {FREE_SELF_PACED[mid]}"


def test_free_self_paced_id_in_scoring_course_ids():
    """The tick's free course id must be in the scoring id-set so a self_attested
    row reconciles to the right mission."""
    m = _scoring_course_ids()
    for mid, free_id in FREE_SELF_PACED.items():
        assert free_id in m[mid], f"{mid}: free self-paced id {free_id} missing from scoring course_ids"


def test_self_attested_row_scores_like_classroom():
    """A self_attested feed row (from the tick round-trip) must score the same as a
    classroom row. Mirrors scoring's `course_type IN ('classroom','self_attested')`."""
    m = _scoring_course_ids()
    free_de = FREE_SELF_PACED["gs_data_engineering"]
    r = score([("u@x.com", free_de, "self_attested", "2026-06-01")], m,
              accepted_types=("classroom", "self_attested"))
    assert r["u@x.com"]["gs_data_engineering"] == (250, "2026-06-01")


def test_scoring_filter_accepts_both_types():
    """scoring_pipeline.py must filter course_type IN ('classroom','self_attested')."""
    src = open(SCORING_PY).read()
    assert "course_type IN ('classroom', 'self_attested')" in src, "scoring filter must accept self_attested"


# --- regression guards for the "points don't persist" bug fixes ------------- #
# These are SOURCE-LEVEL assertions on purpose. The pure-Python `score()` model
# above mirrors the INTENDED logic and can't reproduce Spark's evaluation order
# (Spark evaluates the SELECT projection for every group BEFORE HAVING prunes it),
# which is exactly why the crash slipped past the behavioural tests. So we assert
# the fixed source shape directly.

def test_learner_bonus_uses_try_element_at_not_bare_subscript():
    """Bug 1: the Learner-bonus nth-completion pick must use try_element_at (returns
    NULL out-of-range), NOT a bare `[...]` array subscript. Spark evaluates this
    projection for EVERY group before HAVING drops sub-threshold users, so a user
    with a single course yields a 1-element array and a bare `[threshold-1]` subscript
    throws INVALID_ARRAY_INDEX and fails the whole scoring run."""
    src = open(SCORING_PY).read()
    assert "try_element_at(SORT_ARRAY(COLLECT_LIST(first_completed_at))" in src, \
        "Learner bonus must use try_element_at for the nth-completion pick"
    assert "SORT_ARRAY(COLLECT_LIST(first_completed_at))[" not in src, \
        "bare [] subscript on SORT_ARRAY(COLLECT_LIST(...)) crashes on <threshold groups"


def test_attest_rejects_derived_learner_bonus():
    """Bug 3: the attest endpoint must reject a direct attest of databricks_learner
    (and any training mission with no course id). Attesting it would write course_id=''
    which never reconciles in scoring, so its points would be wiped and never re-derived."""
    src = open(MAIN_PY).read()
    assert 'mission_id == "databricks_learner" or not course_id' in src, \
        "attest must reject the derived Learner bonus / course-less training missions"
    assert "NOT_ATTESTABLE" in src, "expected a NOT_ATTESTABLE guard in the attest endpoint"


def test_attest_gates_award_on_durable_attestation_rowcount():
    """Bug 4: the instant award must be gated on the durable training_attestations
    insert actually inserting a row (rowcount == 1), so a re-tick can't double-credit
    points even if the scoring rebuild transiently cleared the unconstrained
    mission_completions serving row."""
    src = open(MAIN_PY).read()
    assert "newly_attested = cur.rowcount == 1" in src, \
        "attest must derive a durable 'newly attested' signal from the INSERT rowcount"
    assert "if newly_attested and not already:" in src, \
        "the instant award must be gated on newly_attested (durable idempotency)"


def test_roundtrip_runs_before_scoring_not_after():
    """Bug 2: the Lakebase->Delta attestation round-trip must run as its own task
    BEFORE run_scoring (so a tick reconciles the same cycle), and must NOT still live
    inside lakebase_sync.py (which runs AFTER scoring and would wipe the instant rows)."""
    roundtrip_nb = os.path.join(REPO, "notebooks", "roundtrip_attestations.py")
    assert os.path.exists(roundtrip_nb), "roundtrip_attestations.py notebook must exist"
    rt = open(roundtrip_nb).read()
    assert "MERGE INTO" in rt and "training_completions" in rt, \
        "round-trip notebook must MERGE ticks into the Delta training_completions feed"

    sync = open(os.path.join(REPO, "notebooks", "lakebase_sync.py")).read()
    assert "FROM training_attestations" not in sync, \
        "the round-trip must be removed from lakebase_sync.py (it runs after scoring there)"

    dab = open(os.path.join(REPO, "databricks.yml")).read()
    assert "roundtrip_attestations" in dab, "databricks.yml must define the roundtrip_attestations task"
    # run_scoring must depend on the round-trip task.
    run_scoring_idx = dab.find("task_key: run_scoring")
    assert run_scoring_idx != -1
    # the depends_on block immediately after run_scoring must reference the round-trip
    window = dab[run_scoring_idx:run_scoring_idx + 200]
    assert "roundtrip_attestations" in window, "run_scoring must depend_on roundtrip_attestations"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} training-mission logic tests passed.")
