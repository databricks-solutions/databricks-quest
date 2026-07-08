#!/usr/bin/env python3
"""Scaffold a minimal, valid quest pack manifest.

Usage:
    python scripts/new_quest_pack.py --slug my-pack --title "My Pack" [--out PATH]

Emits a skeleton with one quest, one ``sql_assertion`` task (warehouse-independent
``SELECT 1`` so it passes before any resources exist), and one ``manual`` task.
The output lints clean (``python scripts/lint_quest_pack.py <out>``) so authors
have a known-good starting point to edit.

This deliberately stops at *scaffold*; authoring, linting, and importing are the
job of the ``quest-pack-author`` skill (see docs/AUTHORING_QUEST_PACKS.md).
"""

import argparse
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_TEMPLATE = """\
schema_version: "1.0"
pack:
  slug: {slug}
  title: {title}
  version: "0.1.0"
  description: TODO — one sentence on what teams build and learn.
  audience: [solution_architects]
  duration_minutes: 60
  difficulty: beginner
  owner: {owner}

scenario:
  title: TODO — scenario title
  narrative_md: |
    TODO — set the stage. What is the business problem the team is solving?

learning_objectives:
  - TODO — objective one

capabilities_required:
  - sql_warehouse

resources:
  # Per-team namespace; bootstrap creates ${{team_catalog}}.${{team_schema}}.
  team_namespace:
    catalog_template: "${{event_catalog}}"
    schema_template: "team_${{team_slug}}"
  # Optional warm-up data the host bootstrap seeds per team:
  # seed_sql: |
  #   CREATE TABLE IF NOT EXISTS ${{team_catalog}}.${{team_schema}}.warmup (id INT);

scoring_defaults:
  hints_enabled: true

quests:
  - slug: q1-warm-up
    title: Warm up
    category: foundation
    difficulty: beginner
    narrative_md: |
      Confirm your team can run SQL against your assigned warehouse.
    unlock_rule:
      type: always
    tasks:
      - slug: confirm-warehouse
        title: Confirm warehouse binding
        objective: Run a trivial query to confirm your SQL warehouse works.
        instructions_md: |
          Run `SELECT 1` against your assigned warehouse.
        success_criteria_md: |
          The query returns a single row with the value 1.
        points: 50
        validators:
          - id: select-one
            type: sql_assertion
            mode: sync
            statement: |
              SELECT 1 AS ok
            expect:
              operator: "="
              value: 1
            timeout_seconds: 15
        hints:
          - title: It really is this simple
            penalty_points: -5
            body_md: Just run `SELECT 1`.

  - slug: q2-showcase
    title: Showcase
    category: build
    difficulty: beginner
    narrative_md: |
      Build something and have your host confirm it.
    unlock_rule:
      type: quest_completed
      quest_slug: q1-warm-up
    tasks:
      - slug: host-reviewed-build
        title: Build and demo
        objective: TODO — what the team builds and shows the host.
        instructions_md: |
          TODO — steps the team follows.
        success_criteria_md: |
          TODO — what the host looks for to mark this passed.
        points: 100
        manual_validation_required: true
        validators:
          - id: host-review
            type: manual
            mode: sync
        hints:
          - title: Ask your host
            penalty_points: 0
            body_md: Your host confirms this task in the host console.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold a quest pack manifest")
    parser.add_argument("--slug", required=True, help="Pack slug (lowercase-dash)")
    parser.add_argument("--title", required=True, help="Human-readable pack title")
    parser.add_argument("--owner", default="databricks-field", help="Pack owner")
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: quest_packs/built_in/<slug>.yml)",
    )
    args = parser.parse_args()

    if not _SLUG_RE.match(args.slug):
        print(
            f"error: invalid --slug '{args.slug}'. Use lowercase letters, digits, "
            "and single dashes (e.g. 'ai-bi-gameday').",
            file=sys.stderr,
        )
        return 2

    out = args.out or os.path.join(
        REPO_ROOT, "quest_packs", "built_in", f"{args.slug}.yml"
    )
    if os.path.exists(out):
        print(f"error: refusing to overwrite existing file: {out}", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(out), exist_ok=True)
    content = _TEMPLATE.format(slug=args.slug, title=args.title, owner=args.owner)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(content)

    print(f"Wrote {out}")
    print("Next: edit the TODOs, then lint with:")
    print(f"  python scripts/lint_quest_pack.py {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
