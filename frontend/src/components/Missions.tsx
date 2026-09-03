import { useMemo, useState } from 'react'
import { CheckCircle2, Circle, Repeat, Sparkles } from 'lucide-react'
import type { Mission, UserProfile } from '../types'
import { useApi } from '../lib/api'
import { categoryMeta, difficultyForPoints, missionIcon } from '../lib/mission-meta'
import { QuestCard } from './quest/QuestCard'
import { MissionDrawer } from './quest/MissionDrawer'
import { EmptyState, ErrorState, SkeletonCard } from './quest/States'

type MissionsResponse = { missions: Mission[] }

// Canonical mission-pillar tab order. "All Missions" is a frontend-only
// meta-filter, not a pillar value a mission carries. Keep in sync with the
// `track` values assigned in app/main.py's MISSION_DEFINITIONS.
const PILLAR_ORDER = [
  'Getting Started',
  'Business Users',
  'Data Engineering',
  'Data Science',
  'App Dev (Lakebase)',
  'Governance',
]

export default function Missions({
  profile,
  onProfileRefresh,
}: {
  profile?: UserProfile | null
  onProfileRefresh?: () => void
}) {
  const { data, loading, loaded, error, reload } = useApi<MissionsResponse>('/api/missions')
  const [filter, setFilter] = useState<string>('all')
  const [selected, setSelected] = useState<Mission | null>(null)

  const missions = data?.missions ?? []
  // Tabs are mission "pillars" (persona tracks), falling back to category when
  // a mission has no track. Card colors still use category.
  const trackOf = (m: Mission) => m.track || m.category
  const categories = useMemo(() => {
    const present = new Set(missions.map(trackOf))
    // Fixed pillar order first (only pillars that actually have missions),
    // then any stray/untagged category appended at the end so a mission can
    // never silently disappear from the tab list if it's missing a track.
    const ordered = PILLAR_ORDER.filter((p) => present.has(p))
    const leftover = Array.from(present).filter((p) => !PILLAR_ORDER.includes(p))
    return ['all', ...ordered, ...leftover]
  }, [missions])
  const filtered = filter === 'all' ? missions : missions.filter((m) => trackOf(m) === filter)
  const total = missions.length

  // Distinct-mission count + a naive "points from currently-completed missions"
  // sum, derived client-side from this catalog snapshot. Used only as a fallback
  // while `profile` hasn't loaded yet -- once it has, the authoritative numbers
  // below take over. Kept separate on purpose: this local sum can never account
  // for a repeatable mission (daily_driver, ml_practitioner, ...) having been
  // re-earned across multiple past periods, so it will UNDERCOUNT relative to
  // the profile's true lifetime total for any account with repeat history. Using
  // it as anything other than a brief loading placeholder is what caused the
  // Missions page to disagree with the Dashboard.
  const completedLocal = missions.filter((m) => m.status === 'completed').length
  const pointsEarnedLocal = missions.filter((m) => m.status === 'completed').reduce((sum, m) => sum + m.points, 0)

  // Authoritative once profile has loaded: same source the Dashboard reads, so
  // the two pages can never show different numbers for the same account.
  const completed = profile?.missions_completed ?? completedLocal
  const pointsEarned = profile ? profile.total_points : pointsEarnedLocal
  const pct = total > 0 ? Math.round((Math.min(completed, total) / total) * 100) : 0

  const showError = loaded && error && missions.length === 0

  return (
    <div className="mx-auto max-w-[1280px] space-y-5">
      <QuestCard className="quest-topography">
        <div className="relative z-10 flex flex-wrap items-center justify-between gap-6">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#F5B72E]">Mission progress</p>
            <h2 className="mt-2 text-2xl font-bold text-white">{completed} of {total} missions complete</h2>
            <p className="mt-1 text-sm text-slate-300">{pointsEarned.toLocaleString()} points earned all-time</p>
          </div>
          <div className="w-full max-w-sm">
            <div className="mb-2 flex justify-between text-xs text-slate-400">
              <span>Overall completion</span>
              <span className="font-semibold text-[#FF8A3D]">{pct}%</span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-white/10">
              <div className="h-full rounded-full bg-gradient-to-r from-[#FF5F1F] to-[#FFB21F] shadow-[0_0_18px_rgba(255,95,31,0.55)]" style={{ width: `${pct}%` }} />
            </div>
          </div>
        </div>
      </QuestCard>

      <div className="flex flex-wrap gap-2">
        {categories.map((cat) => {
          const active = filter === cat
          const meta = cat === 'all' ? null : categoryMeta(cat)
          return (
            <button
              key={cat}
              onClick={() => setFilter(cat)}
              className={`rounded-xl border px-3.5 py-2 text-xs font-semibold transition-all ${
                active ? 'border-[#FF5F1F]/40 bg-[#FF5F1F]/12 text-white' : 'border-white/10 bg-white/[0.03] text-slate-400 hover:border-white/20 hover:text-slate-200'
              }`}
              style={active && meta ? { borderColor: `${meta.color}66`, background: meta.tint, color: '#fff' } : undefined}
            >
              {cat === 'all' ? 'All Missions' : cat}
            </button>
          )
        })}
      </div>

      {showError ? (
        <ErrorState message={error ?? undefined} onRetry={reload} />
      ) : loading && missions.length === 0 ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={Sparkles}
          title="No missions in this category yet"
          message="Try a different category, or start completing platform actions to unlock more quests."
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((mission) => (
            <MissionTile key={mission.id} mission={mission} onClick={() => setSelected(mission)} />
          ))}
        </div>
      )}

      <MissionDrawer
        mission={selected}
        onClose={() => setSelected(null)}
        onCompleted={() => {
          reload()
          onProfileRefresh?.()
        }}
      />
    </div>
  )
}

function MissionTile({ mission, onClick }: { mission: Mission; onClick: () => void }) {
  const meta = categoryMeta(mission.category)
  const Icon = missionIcon(mission.icon)
  const done = mission.status === 'completed'
  const repeatable = mission.award_type === 'repeatable'

  return (
    <button
      onClick={onClick}
      className="quest-card group p-5 text-left transition-transform duration-200 hover:-translate-y-0.5"
    >
      <div className="relative z-10 flex items-start gap-4">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl" style={{ background: meta.tint, color: meta.color }}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h4 className="truncate text-sm font-semibold text-white">{mission.name}</h4>
            {repeatable && <Repeat className="h-3.5 w-3.5 shrink-0 text-[#8B5CF6]" />}
          </div>
          <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-400">{mission.description}</p>
          <div className="mt-3 flex items-center justify-between">
            <span className="rounded-full px-2 py-0.5 text-[11px] font-medium" style={{ background: meta.tint, color: meta.color }}>
              {difficultyForPoints(mission.points)}
            </span>
            <span className="text-sm font-bold text-[#FF8A3D]">+{mission.points}<span className="ml-1 text-[11px] font-medium text-slate-500">pts</span></span>
          </div>
        </div>
        <div className="shrink-0">
          {done ? <CheckCircle2 className="h-5 w-5 text-[#22C55E]" /> : <Circle className="h-5 w-5 text-slate-600 group-hover:text-slate-400" />}
        </div>
      </div>
    </button>
  )
}
