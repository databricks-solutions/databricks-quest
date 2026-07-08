import type { Badge, UserProfile } from '../types'

// Catalog mirrors the backend BADGE_DEFINITIONS in app/main.py (ids must match
// the badge_id values the scoring pipeline writes, so earned badges light up).
export type BadgeDef = {
  id: string
  name: string
  description: string
  category: string
  image: string
  requirement: string
}

export const BADGE_CATALOG: BadgeDef[] = [
  {
    id: 'platform_explorer',
    name: 'Platform Explorer',
    description: 'Explored core Databricks platform capabilities across multiple product areas.',
    category: 'Foundation',
    image: '/assets/badges/platform-explorer.svg',
    requirement: 'Use 4+ distinct Databricks product areas',
  },
  {
    id: 'pipeline_craftsman',
    name: 'Pipeline Craftsman',
    description: 'Built and operated Lakeflow pipelines, jobs, and workflows.',
    category: 'Data Engineering',
    image: '/assets/badges/pipeline-pioneer.svg',
    requirement: 'Complete 5 pipeline-related missions',
  },
  {
    id: 'consistent_contributor',
    name: 'Consistent Contributor',
    description: 'Maintained a sustained activity streak on the platform.',
    category: 'Engagement',
    image: '/assets/badges/workflow-runner.svg',
    requirement: 'Maintain a 14-day activity streak',
  },
  {
    id: 'ai_pioneer',
    name: 'AI Pioneer',
    description: 'Pioneered AI/ML workloads — model serving, GenAI, and vector search.',
    category: 'AI / ML',
    image: '/assets/badges/ai-trailblazer.svg',
    requirement: 'Complete 3 AI/ML missions',
  },
  {
    id: 'full_stack',
    name: 'Full Stack',
    description: 'Demonstrated breadth across the full Databricks platform.',
    category: 'Mastery',
    image: '/assets/badges/databricks-legend.svg',
    requirement: 'Complete missions in 5+ different categories',
  },
]

export const BADGE_CATEGORY_ORDER = [
  'Foundation',
  'Data Engineering',
  'AI / ML',
  'Engagement',
  'Mastery',
]

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
