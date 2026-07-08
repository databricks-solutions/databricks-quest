# Sample GameDay quest packs

> **Authoring a pack? Start with [`docs/AUTHORING_QUEST_PACKS.md`](../../docs/AUTHORING_QUEST_PACKS.md)** — the single guided scaffold→author→lint→import→version walkthrough. This file covers the run-flow / customization of the built-in packs.

Built-in, ready-to-run quest packs that prove the platform end-to-end. Both lint
and import cleanly and are exercised by `tests/test_sample_packs.py`.

| Pack | File | Slug | Quests / Tasks | Best for |
|------|------|------|----------------|----------|
| AI/BI GameDay | `ai_bi_gameday.yml` | `ai-bi-gameday` | 3 / 6 | SE enablement, customer AI/BI workshops |
| Lakehouse Foundations | `lakehouse_foundations.yml` | `lakehouse-foundations` | 3 / 6 | New-hire onboarding, intro workshops |

Each pack ships: a scenario + narrative, learning objectives, `resources.seed_sql`
(per-team seed data for PR08 bootstrap), three quests with `quest_completed`
unlock gating, six tasks, `sql_assertion` validators (deterministic, read-only),
`databricks_sdk` validators (documenting the intended automated check), `manual`
host-review validators, hints with point penalties, and facilitator notes.

## Run a pack end-to-end

1. **Import** the pack (host console → *Import quest pack*, or the API):

   ```bash
   curl -sS -X POST "$APP_URL/api/host/quest-packs/import" \
     -H 'Content-Type: application/json' \
     --data "$(jq -Rs '{manifest_yaml: .}' samples/packs/ai_bi_gameday.yml)"
   ```

2. **Create an event** from the returned `pack_version_id`, then **create teams**.
3. **Bootstrap resources** (PR08): host console → *Resources* → *Bootstrap*. This
   runs each pack's `resources.seed_sql` into every team's
   `${team_catalog}.${team_schema}`, so the SQL validators have data to check.
   Requires `QUEST_SQL_WAREHOUSE_ID`.
4. **Start the event** and play. The `warm-up` / `govern` quest opens with a
   warehouse-independent connectivity check (`SELECT 1`) so a team can always
   confirm their binding before the timed quests.
5. **Host-reviewed tasks** (dashboard / Genie / design review) appear in the
   host console attempts inspector for approval.

> No warehouse yet? You can still **lint** and **import** packs and run the
> dry-run resource plan; the `sql_assertion` tasks need a warehouse to grade.

## Customize a pack

Packs are plain YAML — copy one and edit. The authoring contract is in
[`../QUEST_PACK_SCHEMA.md`](../QUEST_PACK_SCHEMA.md); the linter
(`POST /api/host/quest-packs/lint`) enforces it and reports actionable findings.

Common edits:

- **Slugs** are immutable identifiers — lowercase, dash-separated
  (`build-the-model`). Unique per quest, and per task within a quest.
- **Points** live on tasks (`points`); hint `penalty_points` should be `<= 0`.
- **Unlock gating** — `unlock_rule: { type: quest_completed, quest_slug: <slug> }`
  must reference a real, earlier quest (and not itself).
- **SQL validators** — `type: sql_assertion` with a read-only single-statement
  `statement` and an `expect` block (`operator` ∈ `= != > >= < <= contains
  not_contains is_true is_false`, or `min_rows`). Use only the server-resolved
  template variables (`${team_catalog}`, `${team_schema}`, `${event_id}`, …); the
  safety layer rejects any other `${...}` slot, so a task can never read another
  team's resources.
- **Seed data** — add statements to `resources.seed_sql` using
  `${team_catalog}`/`${team_schema}`; they run per team at bootstrap.
- **SDK / manual tasks** — pair a `databricks_sdk` validator (documents the
  intended automated check) with a `manual` validator and
  `manual_validation_required: true` so the task resolves via host review until
  an SDK validator ships.

Versions are immutable: bump `pack.version` to publish changes (re-importing the
same version with different content is rejected; identical content is a no-op).
