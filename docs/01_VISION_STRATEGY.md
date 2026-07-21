# 01 — Vision and Strategy

## Vision

Turn Databricks Quest into the **Databricks GameDay platform**: a workspace-native enablement engine that transforms product adoption, training, field demos, and customer workshops into competitive, validated, scenario-driven experiences.

The product should feel like a serious Databricks-native platform, not a novelty leaderboard.

## Product north star

> A Databricks field team can configure and run a customer-relevant GameDay in a Databricks workspace, validate real technical outcomes, and leave with evidence of capability, intent, and adoption readiness.

## Strategic audiences

### 1. Sales and hunter-account teams

Use Quest to create urgency and differentiation in early-stage opportunities.

Examples:

- “Can your team build a governed AI agent in 90 minutes?”
- “Can you modernize a legacy SQL pipeline into Lakeflow?”
- “Can you produce trustworthy executive analytics using governed data?”

The value is not just education. It is **buyer activation**.

### 2. Solution Architects / Specialist SEs

Use Quest packs as reusable field assets instead of one-off demos.

They need:

- repeatable scenarios
- configurable customer context
- low-friction deployment
- automated validation
- event controls
- usable post-event outputs

### 3. Enablement teams

Use Quest for internal skill-building and certification-style exercises.

They need:

- scalable classes
- objective validation
- skills coverage mapping
- progress analytics
- reusable content tracks

### 4. Customers and prospects

Experience Databricks by doing, not watching.

They need:

- a realistic mission narrative
- a safe sandbox
- clear objectives
- enough ambiguity to feel real
- enough guidance to avoid frustration
- scoring that feels fair

## Positioning

Databricks Quest should be positioned as:

> **A live enablement game engine for mastering the lakehouse and data intelligence platform.**

Secondary positioning:

- “GameDay for Databricks”
- “Hands-on platform adoption with automated validation”
- “Live competitive enablement for data, AI, governance, and lakehouse architecture”

## Core modes

### Mode 1 — Adoption Quest

Always-on individual platform adoption scoring.

This preserves the current product and continues to use system tables heavily.

### Mode 2 — GameDay Event

Timed, team-based event with configurable quests, live validations, hints, and leaderboard.

This is the main strategic expansion.

### Mode 3 — Hunter Motion

Customer-specific GameDay events with curated quest packs tied to account strategy.

Examples:

- Retail: customer 360, personalization, inventory, loyalty
- Travel: itinerary intelligence, pricing, offer optimization
- Utilities: asset intelligence, incident response, geospatial operations
- Financial services: governance, fraud, risk, auditability
- SaaS: product telemetry, usage intelligence, data sharing

### Mode 4 — Enablement Certification

Structured training tracks where successful quest completion maps to skill objectives.

## Product pillars

### 1. Configurable quest packs

A quest pack is a versioned content bundle containing:

- scenario narrative
- quests and stages
- objectives
- points
- hints
- validation rules
- seeded data requirements
- expected outputs
- facilitator notes
- prerequisites
- cleanup instructions

### 2. Validated outcomes

A participant should earn points because the system verified that they completed a real objective.

Validation types:

- SQL assertion
- Unity Catalog object check
- Lakeflow job/pipeline check
- dashboard/Genie/API check
- model serving endpoint check
- vector search index check
- notebook execution result
- Python code/test validation
- system-table telemetry
- manual host adjudication

### 3. Live event energy

The app should create urgency:

- countdown timer
- live leaderboard
- recent achievements
- hint market
- bonus rounds
- team status
- host announcements
- endgame leaderboard reveal

### 4. Host control

A GameDay cannot be trusted unless hosts can manage it.

Admin must be able to:

- create and configure events
- import quest packs
- assign teams
- start/pause/freeze events
- observe validation health
- issue announcements
- manually award or revoke points
- export results
- reset resources

### 5. Field-grade repeatability

The field must be able to reuse the platform without engineering help.

Required:

- dry-run mode
- environment precheck
- content validation before event start
- deploy/reset script
- sample packs
- runbooks
- troubleshooting guide
- post-event report template

## Success metrics

### Product metrics

- time to configure a new event
- time to launch an event
- validation success rate
- percentage of quests with automated validators
- time from participant submit to score update
- concurrent users supported
- incident-free event completion rate

### Enablement metrics

- participants completing core quests
- skill areas covered
- improvement between beginner and advanced quests
- hint usage per quest
- drop-off points
- common validation failures

### Sales metrics

- attendees from target accounts
- teams completing business-value quests
- feature intent signals generated
- follow-up meetings booked
- POC acceleration
- champions identified

## Strategy for adoption inside Databricks

1. Build MVP with one strong internal enablement GameDay.
2. Run internally with Databricks teams.
3. Convert learnings into an external-ready field pack.
4. Build three reusable field quest packs:
   - Lakehouse Foundations
   - AI/BI + Genie
   - GenAI/RAG/Agents on Databricks
5. Use with lighthouse hunter accounts.
6. Add content authoring and partner/team extensibility.

## Strategic risk

The main risk is overbuilding a platform before proving event mechanics.

Mitigation:

- keep adoption mode intact
- ship one MVP event flow quickly
- support only 2–3 validator types first
- make content manifest-driven from the beginning
- use Markdown/YAML authoring before building a full quest editor
- build host console before building fancy player UX
