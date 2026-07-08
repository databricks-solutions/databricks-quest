#!/usr/bin/env python3
"""Lint a quest pack manifest locally (no server, no database).

Usage:
    python scripts/lint_quest_pack.py [path/to/pack.yml] [--json]

Defaults to the built-in sample pack. Exits non-zero if there are lint errors,
so it can be used in CI. This validates the same rules the
``POST /api/host/quest-packs/lint`` endpoint applies.
"""

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "app")
DEFAULT_PACK = os.path.join(REPO_ROOT, "quest_packs", "built_in", "ai_bi_gameday.yml")

sys.path.insert(0, APP_DIR)

from services.quest_pack_linter import lint_manifest_text  # noqa: E402
from services.quest_pack_loader import compute_content_hash  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint a quest pack manifest")
    parser.add_argument("path", nargs="?", default=DEFAULT_PACK, help="Manifest YAML path")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args()

    with open(args.path, "r", encoding="utf-8") as fh:
        text = fh.read()

    result = lint_manifest_text(text)
    out = result.to_dict()
    if result.manifest is not None:
        out["content_hash"] = compute_content_hash(result.manifest)

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"File:    {args.path}")
        print(f"OK:      {out['ok']}")
        print(f"Summary: {out['summary']}")
        if out.get("content_hash"):
            print(f"Hash:    {out['content_hash'][:16]}…")
        for e in out["errors"]:
            print(f"  ERROR  {e['loc']}: {e['message']}")
        for w in out["warnings"]:
            print(f"  WARN   {w['loc']}: {w['message']}")
        if out["ok"] and not out["warnings"]:
            print("  (no findings)")

    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
