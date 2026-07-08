# ADR 002 — Configurable Quest Pack Manifests

## Status

Proposed

## Context

Current missions are hard-coded in backend Python and scoring notebook SQL. This prevents field teams from creating or changing GameDay scenarios without code changes.

## Decision

Quest content will be defined in versioned YAML/JSON manifests called **Quest Packs**.

A Quest Pack includes:

- scenario narrative
- quests
- tasks
- points
- validators
- hints
- resources
- facilitator notes
- learning objectives

Imported quest packs are stored in Lakebase with immutable version IDs.

## Consequences

### Positive

- Field teams can create new events without changing app code.
- Packs can be versioned, reviewed, shared, and reused.
- Partner/content-author ecosystem becomes possible.

### Negative

- Need schema validation and linting.
- Need careful validator safety controls.
- Need version immutability once events start.

## Implementation notes

- Start with YAML and `PyYAML`.
- Store canonical manifest JSON in Lakebase.
- Use content hash to detect duplicate imports.
- Add lint endpoint before import.
- Do not build a full UI editor in MVP.
