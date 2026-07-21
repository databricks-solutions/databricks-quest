# 04 — AWS GameDay Research and Translation

## What AWS GameDay is

AWS positions GameDay as a fun, gamified, interactive learning experience. It challenges participants to use AWS solutions to solve real-world technical problems in a team-based setting. It is intentionally different from a traditional workshop because it is open-ended and non-prescriptive.

Key attributes:

- gamified learning
- real-world technical problems
- team-based setting
- open-ended tasks
- non-prescriptive guidance
- exploratory learning
- expert support
- realistic scenarios
- architecture and best-practice reinforcement

## Why the format works

### 1. It creates productive ambiguity

AWS emphasizes that ambiguity and non-prescriptive guidance let teams think creatively. This is important. A GameDay should not feel like a step-by-step lab where everyone follows instructions and arrives at the same answer.

Databricks translation:

- Quest instructions should state outcomes, constraints, and business context.
- Do not provide exact click-paths by default.
- Use hints as a controlled release valve.
- Score validated outcomes, not rote steps.

### 2. It uses a narrative frame

AWS GameDays often use fictional narratives such as `Unicorn.Rentals`. The narrative makes otherwise technical work feel like a mission with stakes.

Databricks translation:

Use customer-relevant story worlds:

- `Orbit Travel` for travel and hospitality
- `Northstar Retail` for retail and CPG
- `AquaWorks Utilities` for utilities and infrastructure
- `HelioBank` for financial services
- `SaaS telemetry company` for product-led growth
- `Global manufacturer` for supply chain and IoT

### 3. It is team-based

Event examples describe teams of 2–4 people, collaboration under time pressure, and leaderboard reveals.

Databricks translation:

- Teams should be a first-class entity.
- Scoring should primarily be team-based in event mode.
- Individuals can still earn badges, but the event energy should come from teams.
- Team resource isolation matters.

### 4. It is hands-on and realistic

AWS GameDay scenarios include deploying infrastructure, troubleshooting misconfigurations, recovering from simulated failures, securing environments, and building with AI services.

Databricks translation:

Quest packs should require users to actually use Databricks:

- create a governed table
- build a pipeline
- debug failed data quality expectations
- create a workflow
- deploy a model or agent endpoint
- build an AI/BI dashboard or Genie space
- implement Unity Catalog governance
- optimize a query or warehouse
- secure data access
- produce a business outcome

### 5. It has expert facilitation

AWS describes access to senior solutions architects and technical account managers for guidance. This matters because the format is intentionally ambiguous.

Databricks translation:

- Host console should support facilitator notes.
- Each quest can include a hidden host guide.
- Facilitators need validation explanations and likely failure modes.
- Hosts need manual adjudication controls.

### 6. It supports themed portfolios

AWS shows multiple GameDay variants across sustainability, generative AI, security, observability, and more.

Databricks translation:

Databricks Quest should have a quest pack library:

- Lakehouse Foundations
- Data Engineering/Lakeflow
- AI/BI and Genie
- GenAI/RAG/Agents
- Governance/Unity Catalog
- Cost and performance optimization
- Data sharing/clean rooms
- Migration modernization
- Industry-specific packs

### 7. It supports partner and co-sell motion

AWS mentions partner benefits including Quest Development Kit and co-sell motion. This is strategically important.

Databricks translation:

Create a **Databricks Quest Development Kit** concept:

- manifest schema
- validator library
- starter templates
- authoring guide
- dry-run validator
- sample datasets
- publishing checklist
- field approval process

## GameDay mechanics to mimic

| AWS GameDay pattern | Databricks Quest equivalent |
|---|---|
| Open-ended challenge | Outcome-based quest instructions |
| Team-based setting | Event teams, team resources, team leaderboard |
| Narrative scenario | Customer/industry-specific story world |
| No step-by-step instructions | Hints and facilitator guide instead of rote walkthrough |
| Real-world technical problems | Databricks tasks validated via APIs, SQL, code, and telemetry |
| Leaderboard | Live scoring, podium, final reveal |
| Expert guidance | Host console, facilitator notes, manual adjudication |
| Multiple portfolios | Quest pack library by product/industry/use case |
| Partner QDK | Databricks Quest Pack SDK and authoring framework |

## Databricks-specific differentiation

Databricks Quest should not only copy AWS GameDay. It should lean into what Databricks uniquely provides:

1. **System tables as telemetry.** Score real usage and detect platform behavior.
2. **Unity Catalog governance.** Validate permissions, lineage, catalog/schema/table creation, masking, and sharing.
3. **Lakeflow and Workflows.** Validate data engineering outcomes.
4. **AI/BI and Genie.** Validate analytics and natural-language BI outcomes.
5. **Model Serving and Mosaic AI.** Validate AI app and agent patterns.
6. **Delta Lake.** Validate data quality, schema evolution, optimization, and table properties.
7. **Databricks Apps.** Let teams build applications inside the platform.
8. **Lakebase.** Provide sub-second operational state for the game engine.

## Recommended Databricks GameDay event format

### Pre-event

- host selects quest pack
- app prechecks workspace capabilities
- participants/teams are imported
- team schemas/catalogs are created
- sample data is seeded
- validators are dry-run
- event URL and instructions are distributed

### Opening briefing — 10 minutes

- explain story world
- explain scoring and hints
- explain rules
- show leaderboard
- explain support model

### Gameplay — 90 to 180 minutes

- teams complete quests
- validators check submitted work
- hints are available with penalties
- leaderboard updates live
- host sends announcements
- bonus quests unlock near the end

### Final freeze — 5 minutes

- event enters scoring freeze
- pending validations finish
- manual adjudications applied

### Debrief — 20 minutes

- final leaderboard reveal
- recap winning approaches
- show common failure patterns
- connect capabilities to customer outcomes
- capture follow-up opportunities

## Recommended scoring mechanics

| Mechanic | Purpose |
|---|---|
| Base points | reward validated completion |
| Bonus points | reward speed, quality, efficiency, governance, or cost control |
| Hint penalties | preserve autonomy while avoiding dead ends |
| Partial credit | avoid all-or-nothing frustration |
| Manual override | allow hosts to resolve edge cases |
| Time freeze | maintain fairness at event close |
| Anti-repeat guard | ensure idempotent scoring |
| Team score | create collaboration energy |
| Individual badges | retain personal recognition |

## Research-driven product implications

1. Build event/team mode before building a quest editor.
2. Build validation before building more badges.
3. Build host console before advanced UX polish.
4. Make the content non-prescriptive by default.
5. Create quest packs around real Databricks product motions.
6. Treat scenario narrative as a core feature, not marketing garnish.
7. Provide a Quest Development Kit so teams and partners can author packs.
