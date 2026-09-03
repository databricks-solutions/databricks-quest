import type { Badge, Mission, UserProfile } from '../types'

// Catalog mirrors the backend BADGE_DEFINITIONS in app/main.py AND the award MERGE
// blocks in notebooks/scoring_pipeline.py. The `id` of each badge MUST equal the
// badge_id the scoring pipeline writes, so earned badges light up. The `criteria`
// here describe how a badge is earned; they drive the "How to earn" checklist and
// progress meter. They must stay in lock-step with the pipeline's award SQL — if the
// pipeline changes what it awards, update the criteria here too.

/**
 * How a badge is earned. Every kind maps to a signal the scoring pipeline already
 * writes, so no badge is decorative/unearnable.
 *  - missions_all:   complete ALL listed missions
 *  - missions_any:   complete ANY ONE of the listed missions
 *  - missions_count: complete at least `need` of the listed missions
 *  - metric:         reach `need` on a numeric profile field (e.g. product breadth)
 *  - legend:         reach `points` OR earn all other badges (whichever comes first)
 */
export type BadgeCriteria =
  | { kind: 'missions_all'; missions: string[] }
  | { kind: 'missions_any'; missions: string[] }
  | { kind: 'missions_count'; missions: string[]; need: number }
  | { kind: 'metric'; field: 'distinct_products_used' | 'total_points'; need: number; unit: string }
  | { kind: 'legend'; points: number }

export type BadgeDef = {
  id: string
  name: string
  description: string
  category: string
  image: string
  /** One-line human summary of the requirement (shown in the modal header). */
  requirement: string
  criteria: BadgeCriteria
}

export const BADGE_CATALOG: BadgeDef[] = [
  {
    id: 'platform_explorer',
    name: 'Platform Explorer',
    description: 'Explored the breadth of the Databricks platform across multiple product areas.',
    category: 'Foundation',
    image: '/assets/badges/platform-explorer.png',
    requirement: 'Use 4+ distinct Databricks products',
    criteria: { kind: 'metric', field: 'distinct_products_used', need: 4, unit: 'products' },
  },
  {
    id: 'pipeline_pioneer',
    name: 'Pipeline Pioneer',
    description: 'Built and operated data pipelines end to end with Lakeflow.',
    category: 'Data Engineering',
    image: '/assets/badges/pipeline-pioneer.png',
    requirement: 'Complete 4 of the core Data Engineering missions',
    criteria: {
      kind: 'missions_count',
      need: 4,
      missions: ['pipeline_builder', 'pipeline_runner', 'auto_loader_pioneer', 'scheduler', 'multi_task_orchestrator', 'job_creator', 'liquid_clustering'],
    },
  },
  {
    id: 'lakeflow_builder',
    name: 'Lakeflow Builder',
    description: 'Created and ran a Lakeflow declarative pipeline.',
    category: 'Data Engineering',
    image: '/assets/badges/lakeflow-builder.png',
    requirement: 'Build a pipeline and complete a successful run',
    criteria: { kind: 'missions_all', missions: ['pipeline_builder', 'pipeline_runner'] },
  },
  {
    id: 'workflow_runner',
    name: 'Workflow Runner',
    description: 'Automated work with a scheduled Lakeflow Job.',
    category: 'Data Engineering',
    image: '/assets/badges/workflow-runner.png',
    requirement: 'Create a Job and put it on a schedule',
    criteria: { kind: 'missions_all', missions: ['job_creator', 'scheduler'] },
  },
  {
    id: 'query_master',
    name: 'Query Master',
    description: 'Put the SQL warehouse to work at analyst scale.',
    category: 'Analytics',
    image: '/assets/badges/query-master.png',
    requirement: 'Hit a weekly SQL query milestone',
    criteria: { kind: 'missions_any', missions: ['data_explorer', 'power_analyst'] },
  },
  {
    id: 'dashboard_creator',
    name: 'Dashboard Creator',
    description: 'Turned data into a shareable Databricks dashboard.',
    category: 'Analytics',
    image: '/assets/badges/dashboard-creator.png',
    requirement: 'Create your first Databricks dashboard',
    criteria: { kind: 'missions_all', missions: ['dashboard_designer'] },
  },
  {
    id: 'ml_practitioner_badge',
    name: 'ML Practitioner',
    description: 'Delivered AI/ML and Data Science workloads on the platform.',
    category: 'AI / ML',
    image: '/assets/badges/ml-practitioner.png',
    requirement: 'Complete 2 of the core Data Science missions',
    criteria: {
      kind: 'missions_count',
      need: 2,
      missions: [
        'model_deployer',
        'ai_function_builder',
        'vector_search_pioneer',
        'mlflow_experimenter',
        'model_registry_curator',
        'feature_store_builder',
      ],
    },
  },
  {
    id: 'unity_catalog_champion',
    name: 'Unity Catalog Champion',
    description: 'Shared and governed data through Unity Catalog.',
    category: 'Governance',
    image: '/assets/badges/unity-catalog-champion.png',
    requirement: 'Complete 2 of the core Governance missions',
    criteria: {
      kind: 'missions_count',
      need: 2,
      missions: [
        'uc_publisher',
        'catalog_architect',
        'external_location_pioneer',
        'lakehouse_federation_pioneer',
        'data_sharer',
      ],
    },
  },
  {
    id: 'governance_guardian',
    name: 'Governance Guardian',
    description: 'Ran production workloads consistently and reliably.',
    category: 'Governance',
    image: '/assets/badges/governance-guardian.png',
    requirement: 'Operate jobs or pipelines on 7 distinct days',
    criteria: { kind: 'missions_all', missions: ['consistent_operator'] },
  },
  {
    id: 'databricks_legend',
    name: 'Databricks Legend',
    description: 'Reached the pinnacle of Databricks mastery.',
    category: 'Mastery',
    image: '/assets/badges/databricks-legend.png',
    requirement: 'Reach Elite level (5,000 points) or collect every other badge',
    criteria: { kind: 'legend', points: 5000 },
  },
]

export const BADGE_CATEGORY_ORDER = [
  'Foundation',
  'Data Engineering',
  'Analytics',
  'AI / ML',
  'Governance',
  'Mastery',
]

// Count of badges other than Databricks Legend — used for the "collect every other
// badge" alternate path on the Legend badge. Derived so it stays correct if the
// catalog grows.
export const NON_LEGEND_BADGE_COUNT = BADGE_CATALOG.filter((b) => b.id !== 'databricks_legend').length

export type DecoratedBadge = BadgeDef & { earned: boolean; earnedAt?: string }

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

/** Decorate the catalog with earned state from the user's real profile badges. */
export function decorateBadges(profile?: UserProfile | null): DecoratedBadge[] {
  const earned = new Map<string, Badge>()
  for (const b of profile?.badges ?? []) {
    if (b.badge_id) {
      earned.set(b.badge_id, b)
      earned.set(slug(b.badge_id), b)
    }
    if (b.badge_name) earned.set(slug(b.badge_name), b)
  }
  return BADGE_CATALOG.map((def) => {
    const hit = earned.get(def.id) || earned.get(slug(def.id)) || earned.get(slug(def.name))
    return { ...def, earned: !!hit, earnedAt: hit?.earned_at }
  })
}

// --- Progress model (drives the "How to earn" checklist + meter) ------------------

export type BadgeStep = { missionId: string; label: string; hint?: string; done: boolean }

export type BadgeProgress = {
  /** Per-mission checklist items (empty for pure metric/legend badges). */
  steps: BadgeStep[]
  /** For count/any rules: how many steps must be done. Equals steps.length for all-of. */
  needed: number
  /** How many steps are currently done. */
  done: number
  /** For metric/legend badges: a single meter. Null for mission-checklist badges. */
  meter: { current: number; target: number; unit: string } | null
  /** 0–100 completion toward the badge. */
  pct: number
  /** Short human summary, e.g. "2 of 4 missions" or "3,200 / 5,000 points". */
  summary: string
}

type ProgressContext = {
  completedMissionIds: Set<string>
  /** Look up a mission's display name + description for checklist labels. */
  missionLookup: (id: string) => Mission | undefined
  profile?: UserProfile | null
  /** How many non-Legend badges the user has earned (for the Legend alternate path). */
  earnedBadgeCount: number
}

function humanizeMissionId(id: string): string {
  return id.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function buildSteps(missionIds: string[], ctx: ProgressContext): BadgeStep[] {
  return missionIds.map((id) => {
    const m = ctx.missionLookup(id)
    return {
      missionId: id,
      label: m?.name ?? humanizeMissionId(id),
      hint: m?.description,
      done: ctx.completedMissionIds.has(id),
    }
  })
}

function pctOf(done: number, needed: number): number {
  if (needed <= 0) return 0
  return Math.max(0, Math.min(100, Math.round((done / needed) * 100)))
}

/** Compute live progress toward a badge from the user's missions + profile. */
export function computeBadgeProgress(def: BadgeDef, ctx: ProgressContext): BadgeProgress {
  const c = def.criteria

  if (c.kind === 'metric') {
    const current = Number(ctx.profile?.[c.field] ?? 0)
    const pct = pctOf(current, c.need)
    return {
      steps: [],
      needed: c.need,
      done: Math.min(current, c.need),
      meter: { current, target: c.need, unit: c.unit },
      pct,
      summary: `${current} / ${c.need} ${c.unit}`,
    }
  }

  if (c.kind === 'legend') {
    const points = Number(ctx.profile?.total_points ?? 0)
    const byPoints = pctOf(points, c.points)
    const byBadges = pctOf(ctx.earnedBadgeCount, NON_LEGEND_BADGE_COUNT)
    // Either path earns it — show whichever is closer to done.
    if (byPoints >= byBadges) {
      return {
        steps: [],
        needed: c.points,
        done: Math.min(points, c.points),
        meter: { current: points, target: c.points, unit: 'points' },
        pct: byPoints,
        summary: `${points.toLocaleString()} / ${c.points.toLocaleString()} points`,
      }
    }
    return {
      steps: [],
      needed: NON_LEGEND_BADGE_COUNT,
      done: ctx.earnedBadgeCount,
      meter: { current: ctx.earnedBadgeCount, target: NON_LEGEND_BADGE_COUNT, unit: 'badges' },
      pct: byBadges,
      summary: `${ctx.earnedBadgeCount} / ${NON_LEGEND_BADGE_COUNT} other badges`,
    }
  }

  // Mission-based rules.
  const steps = buildSteps(c.missions, ctx)
  const doneCount = steps.filter((s) => s.done).length
  const needed = c.kind === 'missions_all' ? steps.length : c.kind === 'missions_any' ? 1 : c.need
  const capped = Math.min(doneCount, needed)
  const noun = needed === 1 ? 'mission' : 'missions'
  return {
    steps,
    needed,
    done: capped,
    meter: null,
    pct: pctOf(capped, needed),
    summary: `${capped} of ${needed} ${noun}`,
  }
}
