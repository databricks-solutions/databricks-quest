# PR02 Prompt — Configurable Quest Packs

## Branch

`feature/gameday-pr02-quest-packs`

## Goal

Add Quest Pack manifest import, linting, versioning, and read APIs.

## Requirements

1. Define Pydantic models for quest pack manifests.
2. Support YAML import using `PyYAML`.
3. Add manifest linter with actionable errors.
4. Store imported packs in `quest_packs`, `quest_pack_versions`, `quests`, `quest_tasks`, `task_validators`, and `task_hints`.
5. Use content hash for duplicate detection.
6. Add APIs:
   - `POST /api/host/quest-packs/lint`
   - `POST /api/host/quest-packs/import`
   - `GET /api/host/quest-packs`
   - `GET /api/host/quest-packs/{pack_id}`
7. Add a built-in sample quest pack under `quest_packs/built_in/`.

## Constraints

- Do not build a full UI editor yet.
- Do not hard-code quest content in Python beyond loading sample file paths.
- Imported pack versions must be immutable.
- Validate unique slugs and references.

## Suggested files

```text
app/models/quest_pack.py
app/services/quest_pack_loader.py
app/services/quest_pack_linter.py
app/repositories/quest_packs.py
quest_packs/built_in/ai_bi_gameday.yml
```

## Acceptance criteria

- Valid YAML imports successfully.
- Invalid YAML returns useful lint errors.
- Imported quest/task/validator counts are returned.
- Pack versions can be listed and retrieved.
- Existing app still works.

## Verification

- Add a small script or manual curl example to lint/import sample pack.
- Run frontend build if any UI changed.
