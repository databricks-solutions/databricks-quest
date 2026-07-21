export interface UserProfile {
  user_id: string
  display_name: string
  total_points: number
  level: string
  level_progress: LevelProgress
  current_streak: number
  max_streak: number
  badge_count: number
  missions_completed: number
  first_activity_date?: string
  last_activity_date?: string
  distinct_products_used: number
  badges: Badge[]
  setup_required?: boolean
  is_admin?: boolean
}

export interface LevelProgress {
  level: string
  current_points: number
  level_floor: number
  level_ceiling: number
  progress_pct: number
}

export interface Mission {
  id: string
  name: string
  description: string
  points: number
  category: string
  track?: string
  award_type: string
  icon: string
  status: 'available' | 'completed'
  completed_at?: string
  doc_url?: string
  learning_resources?: { label: string; url: string }[]
  detection?: string
}

export interface LeaderboardEntry {
  user_id: string
  display_name: string
  total_points: number
  weekly_points: number
  monthly_points: number
  level: string
  all_time_rank: number
  weekly_rank: number
  monthly_rank: number
}

export interface Badge {
  user_id: string
  badge_id: string
  badge_name: string
  badge_icon: string
  earned_at: string
}

export interface Notification {
  notification_type: string
  title: string
  message: string
  mission_id: string
  points: number
  created_at: string
}

export interface AdminStats {
  total_users: number
  total_mission_completions: number
  top_missions: { mission_id: string; mission_name: string; completions: number }[]
  level_distribution: { level: string; cnt: number }[]
  last_refresh: string | null
  setup_required?: boolean
}

export interface PipelineStatus {
  status: string
  last_run: string | null
  total_events_scored: number
}

export interface AdminEntry {
  email: string
  added_by: string | null
  source: string
  added_at: string | null
}

export interface AdminListResponse {
  admins: AdminEntry[]
  caller: string
  caller_is_admin: boolean
}

export type Page = 'dashboard' | 'missions' | 'leaderboard' | 'admin' | 'federation'

// ── Multi-workspace federation (ADR_006) ────────────────────────────────────

export type QuestRole = 'standalone' | 'master' | 'child'

export interface HealthStatus {
  status: 'ok' | 'degraded'
  db_connected: boolean
  event_mode: boolean
  role: QuestRole
  validator_types: string[]
  timestamp: string
}

export interface FederationStatus {
  event_mode?: boolean
  role: QuestRole
  workspace_id: string | null
  event_slug: string | null
  event_id: string | null
  submitted_by?: string
  mapped: boolean
  db_connected?: boolean
  team: { team_id: string; team_name?: string; display_name?: string } | null
}

export interface TeamLeaderboardEntry {
  event_id: string
  team_id: string
  display_name: string | null
  total_points: number
  rank: number | null
  last_scored_at?: string | null
}

export interface FederationLeaderboard {
  leaderboard: TeamLeaderboardEntry[]
  you: TeamLeaderboardEntry | null
  mapped: boolean
  event_id: string | null
  workspace_id?: string | null
}

export interface EventWorkspace {
  workspace_id: string
  event_slug: string | null
  workspace_host: string | null
  app_url: string | null
  app_version: string | null
  status: string
  registered_at: string | null
  last_seen_at: string | null
  scoring_events: number
  points: number
  validations: number
  validation_passes: number
}

export interface UnmappedIdentity {
  event_id: string
  workspace_id: string
  lab_user_email: string
  scoring_events: number
  unattributed_points: number
  last_seen_at: string | null
}

// ── Player gameplay experience (PR05) ───────────────────────────────────────

export type EventStatus =
  | 'draft' | 'ready' | 'active' | 'paused' | 'frozen' | 'completed' | 'archived'

export interface PlayerEvent {
  event_id: string
  slug: string
  title: string
  description?: string | null
  status: EventStatus
  starts_at?: string | null
  ends_at?: string | null
  timezone?: string | null
  pack_title?: string | null
  team_count?: number
}

export interface EventList {
  events: PlayerEvent[]
}

export interface LobbyTeam {
  team_id: string
  name: string
  display_name: string | null
  color: string | null
  members: number
}

export interface EventLobby {
  event: PlayerEvent
  joinable: boolean
  attempts_open: boolean
  is_host: boolean
  team_self_service?: boolean
  teams: LobbyTeam[]
  counts: { participants: number; teams: number; quests: number; tasks: number }
  you: {
    joined: boolean
    participant_id: string | null
    team_id: string | null
    team_name: string | null
  }
}

export interface TeamMember {
  user_id: string
  display_name: string | null
  role: string | null
}

export interface ScoringEventRow {
  scoring_event_id: string
  team_id: string | null
  user_id: string | null
  task_id: string | null
  source_type: string
  points_delta: number
  reason: string
  created_at: string | null
}

export interface TeamDashboard {
  joined: boolean
  team: { team_id: string; name: string; display_name: string; color: string | null } | null
  members: TeamMember[]
  score: number
  rank: number | null
  completed_task_ids: string[]
  progress: { completed_tasks: number; total_tasks: number }
  recent: ScoringEventRow[]
  attempts_open: boolean
}

export interface QuestSummary {
  quest_id: string
  slug: string
  title: string
  category?: string | null
  difficulty?: string | null
  base_points?: number
  sort_order?: number
  task_count: number
  completed_tasks: number
  complete: boolean
}

export interface QuestList {
  quests: QuestSummary[]
  team_id: string | null
  attempts_open: boolean
}

export interface TaskHint {
  hint_id: string
  title: string | null
  // Withheld (null) until the team reveals the hint via the reveal endpoint.
  body_md: string | null
  penalty_points: number | null
  sort_order: number
  revealed: boolean
}

export interface HintRevealResult {
  hint: {
    hint_id: string
    title: string | null
    body_md: string
    penalty_points: number | null
  }
  revealed: boolean
  penalty_applied: number
  newly_applied: boolean
  team_score: number | null
}

export interface QuestTask {
  task_id: string
  slug: string
  title: string
  objective: string
  instructions_md?: string | null
  success_criteria_md?: string | null
  points: number
  sort_order?: number
  validation_mode: string
  complete: boolean
  hints: TaskHint[]
}

export interface QuestDetail {
  quest: {
    quest_id: string
    slug: string
    title: string
    narrative?: string | null
    category?: string | null
    difficulty?: string | null
    base_points?: number
  }
  tasks: QuestTask[]
  team_id: string | null
  attempts_open: boolean
}

export type AttemptStatus = 'passed' | 'failed' | 'manual' | 'error' | 'queued' | 'running'

export interface AttemptResult {
  attempt_id: string
  status: AttemptStatus
  message: string
  points_awarded: number
  already_awarded: boolean
  results: { status: string; message: string }[]
  team_id: string | null
}

export interface Announcement {
  announcement_id: string
  event_id: string
  title: string
  body_md: string
  severity: string
  created_by?: string
  created_at?: string | null
}

// ── Live leaderboard (PR07) ──────────────────────────────────────────────────

export interface LeaderboardRow {
  event_id: string
  team_id: string
  display_name: string | null
  total_points: number
  rank: number | null
  last_scored_at?: string | null
}

export interface RecentScore {
  scoring_event_id: string
  team_id: string | null
  team_name: string | null
  task_id: string | null
  task_title: string | null
  source_type: string
  points_delta: number
  reason: string
  created_at?: string | null
}

export interface EventLeaderboard {
  event: PlayerEvent
  frozen: boolean
  status: string
  leaderboard: LeaderboardRow[]
  recent: RecentScore[]
  you: LeaderboardRow | null
}

// ── Host console (PR06) ──────────────────────────────────────────────────────

export interface HostTeamRow {
  team_id: string
  name: string
  display_name: string | null
  color: string | null
  members: number
  score: number
  rank: number | null
  members_list?: { user_id: string; display_name: string | null }[]
}

export interface HostOverview {
  event: PlayerEvent
  attempts_open: boolean
  allowed_transitions: string[]
  counts: { participants: number; teams: number; quests: number; tasks: number }
  teams: HostTeamRow[]
  attempt_status_counts: Record<string, number>
  announcements: Announcement[]
}

export interface HostAttempt {
  attempt_id: string
  task_id: string
  team_id: string | null
  submitted_by: string
  status: string
  submitted_at: string | null
  completed_at: string | null
  error_message: string | null
  task_title: string | null
  team_name: string | null
}

export interface HostAttemptList {
  attempts: HostAttempt[]
  status_counts: Record<string, number>
}

export interface HostValidationResult {
  validation_result_id: string
  validator_id: string
  status: string
  score_delta: number
  public_message: string | null
  private_message: string | null
  started_at?: string | null
  completed_at?: string | null
}

export interface HostAttemptDetail {
  attempt: Record<string, unknown> & { attempt_id: string; status: string; submitted_by: string }
  results: HostValidationResult[]
}

// ── Resource bootstrap & reset (PR08) ────────────────────────────────────────

export interface ResourceTarget {
  team_id: string | null
  team_name: string | null
  catalog: string
  schema: string
  fqn: string
}

export interface ResourceRow {
  resource_id: string
  team_id: string | null
  resource_type: string
  fqn: string
  status: string
  message: string | null
  updated_at: string | null
}

export interface ResourceHealth {
  namespace: { catalog: string; schema_prefix: string } | null
  namespace_error: string | null
  targets: ResourceTarget[]
  resources: ResourceRow[]
  warehouse_configured: boolean
}

export interface ResourcePlanItem {
  op: string
  team_id: string | null
  resource_type: string
  target: string
  sql: string
  within_namespace: boolean
  error?: string
  status?: string
}

export interface ResourcePlan {
  action: string
  plan: ResourcePlanItem[]
  blockers: ResourcePlanItem[]
  warehouse_configured: boolean
}

// ── Event report (PR11) ──────────────────────────────────────────────────────

export interface ReportSummary {
  event_id: string
  slug: string | null
  title: string | null
  status: string
  starts_at: string | null
  ends_at: string | null
  participants: number
  teams: number
  quests: number
  tasks: number
  attempts: number
  attempts_by_status: Record<string, number>
}

export interface ReportLeaderboardRow {
  rank: number | null
  team_id: string
  team_name: string
  total_points: number
  last_scored_at: string | null
}

export interface ReportCompletionRow {
  team_id: string
  team_name: string
  completed: string[]
  completed_count: number
  total_tasks: number
  completion_pct: number
}

export interface ReportBlocker {
  task_id: string
  task_title: string
  quest_title: string
  solved_teams: number
  total_teams: number
  failed_attempts: number
}

export interface ReportHint {
  team_id: string
  team_name: string
  task_title: string | null
  hint_id: string
  penalty: number
  at: string | null
}

export interface ReportChampion {
  rank: number | null
  team_id: string
  team_name: string
  total_points: number
}

export interface ReportRosterRow {
  user_id: string
  display_name: string
  role: string
  team_id: string | null
  team_name: string
  tasks_passed: number
  attempts_total: number
}

export interface EventReport {
  summary: ReportSummary
  leaderboard: ReportLeaderboardRow[]
  teams: { team_id: string; team_name: string; members: number }[]
  completion_matrix: ReportCompletionRow[]
  roster?: ReportRosterRow[]
  task_catalog: { task_id: string; task_title: string; quest_title: string; points: number }[]
  validation_failures: { task_id: string; task_title: string | null; status: string; attempts: number }[]
  hint_usage: ReportHint[]
  hint_total_penalty: number
  blockers: ReportBlocker[]
  champions: ReportChampion[]
  fastest_team: { team_id: string; team_name: string; first_solves: number } | null
  recommended_follow_ups: string[]
}
