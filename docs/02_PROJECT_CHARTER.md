# 02 — Project Charter

## Project name

**Databricks Quest GameDay Platform**

## Project objective

Level up `databricks-quest` into a configurable, validation-driven, workspace-local GameDay platform for Databricks enablement, events, sales motions, and platform adoption.

## Problem statement

The current app gamifies Databricks usage by scoring system-table activity. This is useful for ongoing adoption but insufficient for large-scale enablement or GameDay-style events because it lacks configurable content, events, teams, validation, resource management, host controls, and event analytics.

## Desired business outcome

Databricks field and enablement teams can run hands-on, competitive technical events at scale, validate real platform outcomes, and generate both learning and sales signals.

## In scope

### Product capabilities

- event mode alongside existing adoption mode
- quest pack manifests
- event/team/participant model
- validation engine
- SQL and Databricks SDK validators
- submission and attempt ledger
- live leaderboard
- hints and penalties
- host console
- manual adjudication
- event announcements
- resource bootstrap/reset primitives
- sample quest packs
- event runbook
- analytics and export

### Technical scope

- FastAPI backend expansion
- Lakebase operational schema
- Delta audit/analytics schema
- frontend pages and components
- Databricks Asset Bundle updates
- Databricks Jobs/Serverless validation worker
- migration/seeding scripts
- tests and local mock mode

## Out of scope for MVP

- full WYSIWYG quest authoring UI
- multi-workspace orchestration
- account-level workspace provisioning
- public SaaS multi-tenant hosting
- payment/reward fulfillment
- LMS integration
- Salesforce integration beyond export-ready artifacts
- complex anti-cheat detection beyond audit logs and validator idempotency

## Stakeholders

| Stakeholder | Interest |
|---|---|
| Field sales | hunter-account activation, compelling demos |
| Solution Architects | repeatable scenario execution |
| Enablement | scaled learning and skill validation |
| Partner teams | partner-authored quest content |
| Customer champions | fun and credible hands-on evaluation |
| Platform admins | safe workspace-local deployment and governance |

## Assumptions

- MVP runs inside a single Databricks workspace.
- Participants authenticate with workspace credentials.
- Event resources can be created in Unity Catalog within configured catalogs/schemas.
- A SQL Warehouse is available for validation queries.
- System tables are available for adoption mode and telemetry validators.
- Lakebase is available for operational state.
- Databricks App compute can host FastAPI + static React frontend.

## Constraints

- Must preserve current deploy path as much as practical.
- Must avoid requiring broad account-admin capabilities for MVP.
- Must support predictable cleanup for event resources.
- Must not hard-code quest content into application source.
- Must keep validation safe, scoped, and auditable.

## MVP definition

A successful MVP allows a host to:

1. Deploy the app to a workspace.
2. Import a quest pack manifest.
3. Create a GameDay event.
4. Register teams and participants.
5. Start the event.
6. Allow users to view quests and submit validation attempts.
7. Automatically validate at least SQL and Databricks SDK checks.
8. Update a live team leaderboard.
9. Use host console to inspect attempts and apply manual overrides.
10. Export a post-event results report.

## Definition of done

- Existing adoption dashboard still works.
- GameDay MVP can be run end-to-end in a fresh workspace.
- At least one sample quest pack is included.
- All validation attempts are persisted.
- Leaderboard is deterministic and explainable.
- Admin can reset an event.
- Host can run a dry-run validation before participants join.
- Documentation includes deploy, run, authoring, and troubleshooting guides.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Validation becomes unsafe or over-privileged | validator allowlist, scoped service principal, read-only SQL by default, audit logs |
| Field teams struggle to author content | YAML schema, examples, linting, dry-run, starter packs |
| Real-time scoring is too slow | Lakebase operational state, async validation jobs, immediate attempt status |
| Scheduled system-table sync makes data stale | move live event state to Lakebase and use incremental sync to Delta |
| Participants game the system | idempotent scoring, attempt limits, manual audit, team resource scoping |
| Too much scope | ship one event mode MVP before advanced content authoring |
