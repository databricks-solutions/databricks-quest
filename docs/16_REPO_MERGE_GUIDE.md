# 16 — Repo Merge Guide

## Recommended merge approach

Do not merge this plan as code. Use it to guide PR creation.

Suggested approach:

```bash
git clone https://github.com/deepbasu123/databricks-quest.git
cd databricks-quest
git checkout -b feature/gameday-pr01-domain-model
```

Then run the relevant agent prompt from `prompts/`.

## Where to place planning docs

Optional:

```text
docs/gameday-levelup/
```

Place the docs there if you want the repo to contain the product spec.

## Expected file evolution

### Backend

Current:

```text
app/main.py
```

Recommended split:

```text
app/main.py
app/db.py
app/auth.py
app/models.py
app/repositories/
app/services/
app/validators/
app/migrations/
```

Do this gradually. Do not split everything in PR01 unless required.

### Frontend

Current:

```text
frontend/src/App.tsx
frontend/src/components/Dashboard.tsx
frontend/src/components/Missions.tsx
frontend/src/components/Leaderboard.tsx
frontend/src/components/AdminPanel.tsx
```

Recommended additions:

```text
frontend/src/api/
frontend/src/routes/
frontend/src/components/event/
frontend/src/components/host/
frontend/src/components/leaderboard/
frontend/src/components/validation/
```

### Quest packs

Add:

```text
quest_packs/
  built_in/
    ai_bi_gameday.yml
    lakehouse_foundations.yml
```

### Notebooks / jobs

Add:

```text
notebooks/validation_worker.py
notebooks/sync_operational_facts.py
notebooks/resource_bootstrap.py
```

## Dependency guidance

Frontend optional additions:

```bash
cd frontend
npm install react-router-dom @tanstack/react-query
```

Backend optional additions:

```text
pydantic
PyYAML
```

Keep dependencies conservative for Databricks App deploy reliability.

## Deployment changes

`deploy.sh` should eventually run:

1. build frontend
2. deploy bundle/app
3. provision Lakebase
4. run migrations
5. import built-in quest packs
6. optionally run adoption scoring
7. optionally run sample event dry-run

## Compatibility guardrails

Preserve:

- `./deploy.sh`
- `frontend npm run build`
- existing adoption endpoints
- current scoring notebook until replacement is ready
- existing README setup path

## Migration strategy

PR01 adds new tables but does not remove current tables.

Later, the current hard-coded mission definitions can be represented as a built-in adoption quest pack:

```text
quest_packs/built_in/adoption_system_tables.yml
```

Only migrate old screens after event mode is stable.

## Recommended first branch

```bash
git checkout -b feature/gameday-pr01-domain-model
```

Use prompt:

```text
prompts/PR01_DOMAIN_MODEL_AND_MIGRATIONS.md
```
