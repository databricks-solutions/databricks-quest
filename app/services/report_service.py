"""Post-event report assembly + rendering (PR11).

``build_report`` is pure: it takes already-fetched rows (event, teams,
leaderboard, task catalog, completion pairs, failures, hint usage, first solves,
status counts) and returns a structured report dict — including derived blockers,
champions, and recommended follow-ups. The render functions turn that dict into
JSON, CSV, or Markdown. Keeping this DB-free makes the shaping and the follow-up
heuristics exhaustively unit-testable.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List, Optional


def _csv_safe(value: Any) -> str:
    """Neutralise CSV formula injection (leading =, +, -, @) in exported cells."""
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@"):
        return "'" + s
    return s


def build_report(
    *,
    event: Dict[str, Any],
    teams: List[Dict[str, Any]],
    leaderboard: List[Dict[str, Any]],
    task_catalog: List[Dict[str, Any]],
    completion_pairs: List[Dict[str, Any]],
    failures: List[Dict[str, Any]],
    hint_usage: List[Dict[str, Any]],
    first_solves: List[Dict[str, Any]],
    status_counts: Dict[str, int],
    counts: Optional[Dict[str, int]] = None,
    participants: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Assemble the structured event report from raw rows."""
    counts = counts or {}
    participants = participants or []
    team_name = {t["team_id"]: (t.get("display_name") or t.get("name") or t["team_id"]) for t in teams}

    # ── Summary ──────────────────────────────────────────────────────────────
    total_tasks = len(task_catalog)
    summary = {
        "event_id": event.get("event_id"),
        "slug": event.get("slug"),
        "title": event.get("title"),
        "status": event.get("status"),
        "starts_at": _iso(event.get("starts_at")),
        "ends_at": _iso(event.get("ends_at")),
        "participants": counts.get("participants", 0),
        "teams": counts.get("teams", len(teams)),
        "quests": counts.get("quests", len({t["quest_slug"] for t in task_catalog})),
        "tasks": counts.get("tasks", total_tasks),
        "attempts": sum(status_counts.values()),
        "attempts_by_status": status_counts,
    }

    # ── Completion matrix (team × task) ──────────────────────────────────────
    completed_by_team: Dict[str, set] = {}
    for row in completion_pairs:
        completed_by_team.setdefault(row["team_id"], set()).add(row["task_id"])

    matrix_rows = []
    for t in teams:
        tid = t["team_id"]
        done = completed_by_team.get(tid, set())
        matrix_rows.append({
            "team_id": tid,
            "team_name": team_name.get(tid, tid),
            "completed": sorted(done & {tc["task_id"] for tc in task_catalog}),
            "completed_count": len([tc for tc in task_catalog if tc["task_id"] in done]),
            "total_tasks": total_tasks,
            "completion_pct": round(100 * len([tc for tc in task_catalog if tc["task_id"] in done]) / total_tasks, 1) if total_tasks else 0.0,
        })

    # ── Blockers: tasks with the fewest completions / most failures ──────────
    completions_per_task: Dict[str, int] = {tc["task_id"]: 0 for tc in task_catalog}
    for row in completion_pairs:
        if row["task_id"] in completions_per_task:
            completions_per_task[row["task_id"]] += 1
    failures_per_task: Dict[str, int] = {}
    for f in failures:
        failures_per_task[f["task_id"]] = failures_per_task.get(f["task_id"], 0) + int(f.get("attempts") or 0)

    n_teams = max(1, len(teams))
    blockers = []
    for tc in task_catalog:
        solved = completions_per_task.get(tc["task_id"], 0)
        failed = failures_per_task.get(tc["task_id"], 0)
        # A task is a blocker if a minority of teams solved it OR it drew failures.
        if solved < n_teams or failed > 0:
            blockers.append({
                "task_id": tc["task_id"],
                "task_title": tc["task_title"],
                "quest_title": tc["quest_title"],
                "solved_teams": solved,
                "total_teams": len(teams),
                "failed_attempts": failed,
            })
    # Worst first: least solved, then most failed.
    blockers.sort(key=lambda b: (b["solved_teams"], -b["failed_attempts"]))

    # ── Hint usage ───────────────────────────────────────────────────────────
    hints = [
        {
            "team_id": h.get("team_id"),
            "team_name": h.get("team_name") or team_name.get(h.get("team_id"), h.get("team_id")),
            "task_title": h.get("task_title"),
            "hint_id": h.get("hint_id"),
            "penalty": int(h.get("points_delta") or 0),
            "at": _iso(h.get("created_at")),
        }
        for h in hint_usage
    ]
    hint_total_penalty = sum(h["penalty"] for h in hints)

    # ── Champions / high performers ──────────────────────────────────────────
    ranked = sorted(
        leaderboard, key=lambda r: (r.get("rank") if r.get("rank") is not None else 1e9)
    )
    champions = [
        {
            "rank": r.get("rank"),
            "team_id": r.get("team_id"),
            "team_name": r.get("display_name") or team_name.get(r.get("team_id"), r.get("team_id")),
            "total_points": int(r.get("total_points") or 0),
        }
        for r in ranked[:3]
    ]
    first_blood_count: Dict[str, int] = {}
    for fs in first_solves:
        first_blood_count[fs["team_id"]] = first_blood_count.get(fs["team_id"], 0) + 1
    fastest_team_id = max(first_blood_count, key=first_blood_count.get) if first_blood_count else None

    # ── Participant roster (team, role, personally-submitted task stats) ──────
    roster = [
        {
            "user_id": p.get("user_id"),
            "display_name": p.get("display_name") or p.get("user_id"),
            "role": p.get("role") or "player",
            "team_id": p.get("team_id"),
            "team_name": p.get("team_name") or team_name.get(p.get("team_id")) or "—",
            "tasks_passed": int(p.get("tasks_passed") or 0),
            "attempts_total": int(p.get("attempts_total") or 0),
        }
        for p in participants
    ]

    # ── Recommended follow-ups (heuristics for account/enablement motion) ────
    follow_ups: List[str] = []
    concerns = False
    if blockers and blockers[0]["solved_teams"] < n_teams:
        concerns = True
        worst = blockers[0]
        follow_ups.append(
            f"Reinforce '{worst['task_title']}' ({worst['quest_title']}): only "
            f"{worst['solved_teams']}/{worst['total_teams']} teams completed it — a "
            "strong candidate for a follow-up enablement session or workshop."
        )
    if hint_total_penalty < 0:
        concerns = True
        follow_ups.append(
            f"Teams leaned on hints ({len(hints)} reveals, {hint_total_penalty} pts). "
            "Review the most-hinted tasks for documentation or product gaps to raise "
            "with the account team."
        )
    weak_teams = [m for m in matrix_rows if m["completion_pct"] < 50.0]
    if weak_teams:
        concerns = True
        names = ", ".join(sorted(m["team_name"] for m in weak_teams))
        follow_ups.append(
            f"Lower-completion teams ({names}) are good targets for a 1:1 follow-up "
            "or a guided replay of the scenario."
        )
    if champions:
        follow_ups.append(
            f"Recognise top performers ({', '.join(c['team_name'] for c in champions)}) — "
            "potential champions/references for the account."
        )
    if not concerns:
        follow_ups.append("No blockers detected — consider a harder pack or a timed finale next time.")

    return {
        "summary": summary,
        "leaderboard": [
            {
                "rank": r.get("rank"),
                "team_id": r.get("team_id"),
                "team_name": r.get("display_name") or team_name.get(r.get("team_id"), r.get("team_id")),
                "total_points": int(r.get("total_points") or 0),
                "last_scored_at": _iso(r.get("last_scored_at")),
            }
            for r in ranked
        ],
        "teams": [
            {
                "team_id": t["team_id"],
                "team_name": team_name.get(t["team_id"], t["team_id"]),
                "members": int(t.get("members") or 0),
            }
            for t in teams
        ],
        "completion_matrix": matrix_rows,
        "roster": roster,
        "task_catalog": [
            {
                "task_id": tc["task_id"],
                "task_title": tc["task_title"],
                "quest_title": tc["quest_title"],
                "points": int(tc.get("points") or 0),
            }
            for tc in task_catalog
        ],
        "validation_failures": [
            {
                "task_id": f.get("task_id"),
                "task_title": f.get("task_title"),
                "status": f.get("status"),
                "attempts": int(f.get("attempts") or 0),
            }
            for f in failures
        ],
        "hint_usage": hints,
        "hint_total_penalty": hint_total_penalty,
        "blockers": blockers,
        "champions": champions,
        "fastest_team": {
            "team_id": fastest_team_id,
            "team_name": team_name.get(fastest_team_id, fastest_team_id),
            "first_solves": first_blood_count.get(fastest_team_id, 0),
        } if fastest_team_id else None,
        "recommended_follow_ups": follow_ups,
    }


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


# ── Renderers ────────────────────────────────────────────────────────────────


def render_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, default=str)


def render_csv(report: Dict[str, Any]) -> str:
    """A team-centric CSV: rank, team, points, completion, hints — plus a task
    completion matrix. Useful to drop into a spreadsheet for account follow-up.
    """
    out = io.StringIO()
    writer = csv.writer(out)

    matrix_by_team = {m["team_id"]: m for m in report["completion_matrix"]}
    hints_by_team: Dict[str, int] = {}
    for h in report["hint_usage"]:
        hints_by_team[h["team_id"]] = hints_by_team.get(h["team_id"], 0) + 1

    tasks = report["task_catalog"]
    header = ["rank", "team", "points", "tasks_completed", "total_tasks", "completion_pct", "hints_used"]
    header += [t["task_title"] for t in tasks]
    writer.writerow(header)

    for row in report["leaderboard"]:
        tid = row["team_id"]
        m = matrix_by_team.get(tid, {})
        done = set(m.get("completed", []))
        line = [
            _csv_safe(row.get("rank")),
            _csv_safe(row["team_name"]),
            _csv_safe(row["total_points"]),
            _csv_safe(m.get("completed_count", 0)),
            _csv_safe(m.get("total_tasks", len(tasks))),
            _csv_safe(m.get("completion_pct", 0)),
            _csv_safe(hints_by_team.get(tid, 0)),
        ]
        line += ["1" if t["task_id"] in done else "0" for t in tasks]
        writer.writerow(line)

    # Participant roster as a clearly-delimited second block.
    roster = report.get("roster") or []
    if roster:
        writer.writerow([])
        writer.writerow(["Participant roster"])
        writer.writerow(["user", "display_name", "role", "team", "tasks_passed", "attempts"])
        for r in roster:
            writer.writerow([
                _csv_safe(r.get("user_id")),
                _csv_safe(r.get("display_name")),
                _csv_safe(r.get("role")),
                _csv_safe(r.get("team_name")),
                _csv_safe(r.get("tasks_passed")),
                _csv_safe(r.get("attempts_total")),
            ])
    return out.getvalue()


def render_markdown(report: Dict[str, Any]) -> str:
    s = report["summary"]
    lines: List[str] = []
    lines.append(f"# Event report — {s.get('title') or s.get('slug')}")
    lines.append("")
    lines.append(f"- **Status:** {s.get('status')}")
    lines.append(f"- **Teams:** {s.get('teams')}  •  **Participants:** {s.get('participants')}")
    lines.append(f"- **Quests:** {s.get('quests')}  •  **Tasks:** {s.get('tasks')}")
    lines.append(f"- **Attempts:** {s.get('attempts')} ({_status_counts_str(s.get('attempts_by_status', {}))})")
    lines.append("")

    lines.append("## Leaderboard")
    lines.append("")
    lines.append("| Rank | Team | Points |")
    lines.append("|---:|---|---:|")
    for r in report["leaderboard"]:
        lines.append(f"| {r.get('rank') if r.get('rank') is not None else '—'} | {r['team_name']} | {r['total_points']} |")
    lines.append("")

    if report["champions"]:
        lines.append("## Champions / high performers")
        lines.append("")
        for c in report["champions"]:
            lines.append(f"- **#{c.get('rank')} {c['team_name']}** — {c['total_points']} pts")
        ft = report.get("fastest_team")
        if ft:
            lines.append(f"- **Fastest team:** {ft['team_name']} ({ft['first_solves']} first solves)")
        lines.append("")

    lines.append("## Quest completion")
    lines.append("")
    lines.append("| Team | Completed | % |")
    lines.append("|---|---:|---:|")
    for m in report["completion_matrix"]:
        lines.append(f"| {m['team_name']} | {m['completed_count']}/{m['total_tasks']} | {m['completion_pct']}% |")
    lines.append("")

    if report["blockers"]:
        lines.append("## Blockers (hardest tasks)")
        lines.append("")
        lines.append("| Task | Quest | Solved | Failed attempts |")
        lines.append("|---|---|---:|---:|")
        for b in report["blockers"][:10]:
            lines.append(f"| {b['task_title']} | {b['quest_title']} | {b['solved_teams']}/{b['total_teams']} | {b['failed_attempts']} |")
        lines.append("")

    if report["hint_usage"]:
        lines.append(f"## Hint usage ({len(report['hint_usage'])} reveals, {report['hint_total_penalty']} pts)")
        lines.append("")
        lines.append("| Team | Task | Penalty |")
        lines.append("|---|---|---:|")
        for h in report["hint_usage"]:
            lines.append(f"| {h['team_name']} | {h.get('task_title') or '—'} | {h['penalty']} |")
        lines.append("")

    roster = report.get("roster") or []
    if roster:
        lines.append(f"## Participant roster ({len(roster)})")
        lines.append("")
        lines.append("| Participant | Role | Team | Tasks passed | Attempts |")
        lines.append("|---|---|---|---:|---:|")
        for r in roster:
            lines.append(
                f"| {r['display_name']} | {r['role']} | {r['team_name']} | "
                f"{r['tasks_passed']} | {r['attempts_total']} |"
            )
        lines.append("")

    lines.append("## Recommended follow-ups")
    lines.append("")
    for f in report["recommended_follow_ups"]:
        lines.append(f"- {f}")
    lines.append("")
    return "\n".join(lines)


def _status_counts_str(counts: Dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
