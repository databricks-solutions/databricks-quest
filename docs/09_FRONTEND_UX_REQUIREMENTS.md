# 09 — Frontend UX Requirements

## UX target

The upgraded app should feel like a Databricks-native event control system and gameplay interface. It should be premium, credible, and energetic without becoming childish.

## Product areas

### 1. Landing / mode selector

Purpose:

- show current adoption dashboard
- show active GameDay events
- let user enter an event lobby
- let hosts manage events

Key elements:

- active event cards
- adoption progress card
- upcoming events
- host shortcuts

### 2. Event lobby

Purpose:

- orient players before event starts
- show narrative, timing, rules, team assignment
- run prechecks

Key elements:

- event hero
- countdown
- team card
- rules
- capabilities required
- host announcements
- “Join Event” / “Enter Game” CTA

### 3. Team gameplay dashboard

Purpose:

- answer “what should my team do next?”

Key elements:

- team score and rank
- countdown timer
- quest path/progress map
- recommended next task
- recent validation results
- leaderboard teaser
- announcements
- hint usage

### 4. Quest runner

Purpose:

- focus team on one quest/task
- provide enough context without step-by-step answers
- submit work for validation

Key elements:

- narrative panel
- objective and success criteria
- task list
- validation submission form
- “Validate” button
- validation result panel
- hints drawer
- facilitator-safe details hidden from players

### 5. Live leaderboard

Purpose:

- create event energy and competition

Key elements:

- team ranking
- score movement
- recent achievements
- first-blood bonuses
- final podium
- freeze state

### 6. Host console

Purpose:

- run the event confidently

Key elements:

- event state controls
- participant/team health
- validation queue
- failed validations
- manual adjustments
- announcements
- resource status
- dry-run panel
- export report

### 7. Quest pack admin

Purpose:

- import, validate, and manage content

Key elements:

- upload/paste manifest
- lint results
- quest/validator summary
- warnings/errors
- version history
- publish/archive controls

## Required UX states

Every major view needs:

- loading state
- empty state
- error state
- permission denied state
- event not started state
- event paused state
- event frozen state
- completed event state
- validation queued state
- validation running state
- validation passed state
- validation failed state
- validation errored state

## Design system requirements

Use the Databricks Quest brand kit direction:

- dark graphite base
- restrained Databricks orange accents
- semantic colors by quest category
- glass cards and subtle topography/constellation motif
- premium badges, not cartoon icons
- clean enterprise typography

## Player screen hierarchy

The player should always know:

1. How much time remains.
2. What team they are on.
3. Their current score and rank.
4. What is completed.
5. What to do next.
6. Whether a validation passed or failed.
7. What hint options exist.

## Host screen hierarchy

The host should always know:

1. Is the event healthy?
2. Are validators working?
3. Are teams stuck?
4. Are resources ready?
5. What needs manual intervention?
6. What announcements have been sent?
7. Can the event be safely frozen/completed?

## Frontend implementation recommendation

### Introduce route structure

Recommended dependency:

```bash
npm install react-router-dom @tanstack/react-query
```

Recommended routes:

```text
/
/adoption
/events
/events/:eventId
/events/:eventId/play
/events/:eventId/quests/:questId
/events/:eventId/leaderboard
/host
/host/events/:eventId
/host/quest-packs
```

### Introduce API client

```text
frontend/src/api/client.ts
frontend/src/api/events.ts
frontend/src/api/questPacks.ts
frontend/src/api/validation.ts
frontend/src/api/leaderboard.ts
```

### Component structure

```text
components/event/
  EventCard.tsx
  EventLobbyHero.tsx
  EventCountdown.tsx
  TeamCard.tsx
  QuestProgressMap.tsx
  ValidationStatus.tsx
  HintDrawer.tsx

components/host/
  EventControlPanel.tsx
  ValidationQueue.tsx
  TeamProgressTable.tsx
  AnnouncementComposer.tsx
  ManualScoreAdjustment.tsx
  ResourceHealthPanel.tsx

components/leaderboard/
  LiveLeaderboard.tsx
  Podium.tsx
  RecentAchievements.tsx
```

## Interaction patterns

### Submit validation

1. Player opens task.
2. Player reviews required submission fields.
3. Player clicks Validate.
4. UI shows running state.
5. If passed, confetti/points animation and leaderboard update.
6. If failed, show safe failure message and next action.
7. Host can see private diagnostic.

### Take hint

1. Player opens hints drawer.
2. UI shows penalty before revealing hint.
3. Player confirms.
4. Hint reveals.
5. Penalty scoring event is written.

### Event start

1. Host starts event.
2. Lobby becomes gameplay.
3. Timer starts.
4. Quests unlock.
5. Announcement posted automatically.

### Event freeze

1. Host freezes event.
2. New submissions blocked.
3. Running validations complete.
4. Leaderboard finalizes.
5. Final reveal view appears.

## MVP frontend deliverables

- Event lobby
- Team gameplay dashboard
- Quest runner
- Live leaderboard
- Host console
- Quest pack import/lint page

Adoption mode can keep current screens initially.
