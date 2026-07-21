# 00 — Executive Summary

## Current state

`databricks-quest` is a strong proof-of-concept: a React + FastAPI Databricks App that reads scored activity from Lakebase, where scoring is produced by a scheduled Databricks notebook over system tables. It has the right primitives for an adoption leaderboard:

- Databricks App hosting
- React frontend
- FastAPI backend
- Delta scoring tables
- Lakebase for fast reads
- Databricks Asset Bundle deployment
- scheduled scoring every four hours
- system-table-derived mission completion

However, it is not yet a GameDay platform.

The current product is **user telemetry gamification**. The target product is **configurable event-based enablement infrastructure**.

## Strategic opportunity

AWS GameDay has a format worth emulating: open-ended, team-based, gamified, realistic technical challenges with ambiguity, time pressure, expert support, and leaderboard-driven energy. Databricks Quest can become the Databricks equivalent: a field-ready tool for:

1. **Enablement:** teaching platform skills through live challenges.
2. **Large-scale events:** running hundreds of participants through controlled missions.
3. **Hunter account motions:** letting prospects experience Databricks capabilities in a hands-on, competitive scenario.
4. **Sales scale:** packaging demos and workshops as reusable quest packs.
5. **Adoption analytics:** showing what people actually learned, built, validated, and struggled with.

## Target product definition

**Databricks Quest GameDay** is a workspace-local Databricks App for running live, configurable, team-based quest events on Databricks.

It supports:

- configurable quest packs
- event creation and scheduling
- teams and participants
- quest narratives and challenge stages
- automated validation via system tables, SQL assertions, Databricks SDK checks, notebook validators, REST checks, and code-level validators
- live leaderboard
- hints, penalties, manual adjudication
- admin/host console
- event telemetry and post-event report
- reusable field motion templates

## Non-negotiable product principles

1. **Configurable by content, not code.** Quest packs must be defined outside Python/TS source code.
2. **Validation-first.** Completion must be verified, not inferred only from broad system-table activity.
3. **Workspace-local first.** The platform must run inside a Databricks workspace without requiring account-wide admin primitives for the MVP.
4. **Event mode and adoption mode must coexist.** The current always-on adoption leaderboard is valuable and should not be destroyed.
5. **Host trust matters.** Admins need visibility into validation health, scoring events, manual overrides, participant progress, and resource readiness.
6. **Field usability matters.** A sales/SA team must be able to clone, configure, dry-run, and run a GameDay without engineering support.

## Major gaps to close

| Area | Current | Required |
|---|---|---|
| Quest definition | hard-coded missions in backend and notebook | versioned manifest-driven quest packs |
| Event model | none | events, teams, participants, phases, schedules |
| Validation | system-table inference only | pluggable validation engine with code/assertion validators |
| Scoring | scheduled batch and points totals | attempt ledger, real-time scoring events, hints, penalties, manual adjudication |
| Admin | high-level stats | host console, event controls, content import, validation monitor |
| UX | dashboard, missions, leaderboard, admin | lobby, team dashboard, quest runner, host console, event control tower |
| Data model | six summary tables | operational event model plus audit and analytics tables |
| Large-scale ops | one workspace deploy | dry-run, reset, team provisioning, event export/reporting |
| Hunter account motion | generic Databricks adoption | configurable industry narratives and outcome reports |

## Recommended delivery structure

Deliver as **12 PR-aligned sprints**:

1. Domain model and migrations
2. Configurable quest packs
3. Validation engine core
4. Event and team management
5. Player gameplay experience
6. Admin host console
7. Live scoring and leaderboard
8. Resource bootstrap and reset
9. Sample GameDay packs
10. Security, observability, and audit
11. Field reporting and hunter-account signaling
12. Hardening, release, docs, and operating model

## Recommended first milestone

The first milestone should be a **single workspace-local GameDay MVP**:

- one event
- multiple teams
- manifest-driven quest pack
- 5–8 quests
- SQL + Databricks SDK validators
- live team leaderboard
- host console
- audit trail
- post-event summary

That will prove the architectural shift without trying to boil the ocean.
