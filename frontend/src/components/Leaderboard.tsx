import { useMemo, useState } from 'react'
import { Crown, Medal, Trophy, Users } from 'lucide-react'
import type { LeaderboardEntry, UserProfile } from '../types'
import { useApi } from '../lib/api'
import { QuestCard } from './quest/QuestCard'
import { EmptyState, ErrorState, Skeleton } from './quest/States'

type LeaderboardResponse = { leaderboard: LeaderboardEntry[] }
type View = 'weekly' | 'all' | 'teams'

const VIEWS: { id: View; label: string }[] = [
  { id: 'weekly', label: 'This Week' },
  { id: 'all', label: 'All Time' },
  { id: 'teams', label: 'Teams' },
]

const LEVEL_BADGE: Record<string, string> = {
  Bronze: 'bg-amber-800/30 text-amber-500 border-amber-700/40',
  Silver: 'bg-slate-600/30 text-slate-300 border-slate-500/40',
  Gold: 'bg-[#F5B72E]/15 text-[#F5B72E] border-[#F5B72E]/40',
  Platinum: 'bg-violet-500/20 text-violet-300 border-violet-500/40',
  Elite: 'bg-[#FF5F1F]/20 text-[#FF8A3D] border-[#FF5F1F]/40',
}

const LEVEL_ORDER = ['Elite', 'Platinum', 'Gold', 'Silver', 'Bronze']

function RankIcon({ rank }: { rank: number }) {
  if (rank === 1) return <Crown className="h-5 w-5 text-[#F5B72E]" />
  if (rank === 2) return <Medal className="h-5 w-5 text-slate-300" />
  if (rank === 3) return <Medal className="h-5 w-5 text-amber-600" />
  return <span className="w-5 text-center font-mono text-sm text-slate-500">{rank}</span>
}

export default function Leaderboard({ profile }: { profile?: UserProfile | null }) {
  const [view, setView] = useState<View>('weekly')
  const period = view === 'teams' ? 'all' : view
  const { data, loading, loaded, error, reload } = useApi<LeaderboardResponse>(
    `/api/leaderboard?period=${period}`,
  )

  const entries = data?.leaderboard ?? []
  const isYou = (e: LeaderboardEntry) =>
    !!profile && (e.user_id === profile.user_id || e.display_name === profile.display_name)

  const points = (e: LeaderboardEntry) => (view === 'weekly' ? e.weekly_points : e.total_points)
  const rank = (e: LeaderboardEntry) => (view === 'weekly' ? e.weekly_rank : e.all_time_rank)
  const sorted = useMemo(() => [...entries].sort((a, b) => rank(a) - rank(b)), [entries, view])

  const you = sorted.find(isYou)
  const showError = loaded && error && entries.length === 0

  return (
    <div className="mx-auto max-w-[1100px] space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-xl border border-white/10 bg-white/[0.03] p-1">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              onClick={() => setView(v.id)}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                view === v.id ? 'bg-[#FF5F1F]/15 text-white shadow-inner' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {v.label}
            </button>
          ))}
        </div>
        {you && view !== 'teams' && (
          <div className="rounded-xl border border-[#FF5F1F]/30 bg-[#FF5F1F]/[0.08] px-4 py-2 text-sm">
            <span className="text-slate-400">Your rank</span>
            <span className="ml-2 font-bold text-[#FF8A3D]">#{rank(you)}</span>
            <span className="ml-2 text-slate-300">· {points(you).toLocaleString()} pts</span>
          </div>
        )}
      </div>

      {showError ? (
        <ErrorState message={error ?? undefined} onRetry={reload} />
      ) : loading && entries.length === 0 ? (
        <LeaderboardSkeleton />
      ) : sorted.length === 0 ? (
        <EmptyState icon={Trophy} title="No leaderboard data yet" message="Once the scoring pipeline runs, rankings will appear here." />
      ) : view === 'teams' ? (
        <TeamsView entries={sorted} />
      ) : (
        <RankingView sorted={sorted} isYou={isYou} points={points} rank={rank} />
      )}

      <SwagPanel />
    </div>
  )
}

function RankingView({
  sorted,
  isYou,
  points,
  rank,
}: {
  sorted: LeaderboardEntry[]
  isYou: (e: LeaderboardEntry) => boolean
  points: (e: LeaderboardEntry) => number
  rank: (e: LeaderboardEntry) => number
}) {
  const podium = sorted.slice(0, 3)
  return (
    <>
      {podium.length >= 3 && (
        <div className="grid grid-cols-3 gap-4">
          {[podium[1], podium[0], podium[2]].map((entry, i) => {
            const place = [2, 1, 3][i]
            const prize = place === 1 ? 'Champion recognition' : 'Weekly recognition'
            return (
              <div
                key={entry.user_id}
                className={`quest-card p-5 text-center ${place === 1 ? 'border-[#F5B72E]/40 lg:-mt-3' : 'lg:mt-2'}`}
              >
                <div className="relative z-10">
                  <div className={`mx-auto flex h-14 w-14 items-center justify-center rounded-full text-lg font-bold ${
                    place === 1 ? 'bg-[#F5B72E]/20 text-[#F5B72E] ring-2 ring-[#F5B72E]/40' : place === 2 ? 'bg-slate-600/30 text-slate-300' : 'bg-amber-800/30 text-amber-600'
                  }`}>
                    {(entry.display_name || '?')[0].toUpperCase()}
                  </div>
                  <div className="mt-3 flex justify-center"><RankIcon rank={place} /></div>
                  <p className="mt-2 truncate text-sm font-semibold text-white">{entry.display_name}{isYou(entry) ? ' (You)' : ''}</p>
                  <p className="mt-1 text-lg font-bold text-[#FF8A3D]">{points(entry).toLocaleString()}</p>
                  <p className="text-xs text-slate-500">points</p>
                  <span className={`mt-2 inline-block rounded-full border px-2 py-0.5 text-xs ${LEVEL_BADGE[entry.level] || LEVEL_BADGE.Bronze}`}>{entry.level}</span>
                  <p className="mt-2 text-xs font-medium text-slate-400">{prize}</p>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <QuestCard className="p-0">
        <div className="relative z-10 grid grid-cols-12 gap-2 border-b border-white/10 px-5 py-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
          <div className="col-span-1">Rank</div>
          <div className="col-span-5">Explorer</div>
          <div className="col-span-3 text-center">Level</div>
          <div className="col-span-3 text-right">Points</div>
        </div>
        <div className="relative z-10 divide-y divide-white/5">
          {sorted.map((entry) => {
            const you = isYou(entry)
            return (
              <div key={entry.user_id} className={`grid grid-cols-12 items-center gap-2 px-5 py-3.5 transition ${you ? 'bg-[#FF5F1F]/[0.08]' : 'hover:bg-white/[0.03]'}`}>
                <div className="col-span-1 flex items-center"><RankIcon rank={rank(entry)} /></div>
                <div className="col-span-5 flex items-center gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/[0.06] text-xs font-bold text-[#FF8A3D]">
                    {(entry.display_name || '?')[0].toUpperCase()}
                  </div>
                  <span className={`truncate text-sm ${you ? 'font-semibold text-[#FF8A3D]' : 'text-slate-200'}`}>{entry.display_name}{you ? ' (You)' : ''}</span>
                </div>
                <div className="col-span-3 text-center">
                  <span className={`rounded-full border px-2 py-0.5 text-xs ${LEVEL_BADGE[entry.level] || LEVEL_BADGE.Bronze}`}>{entry.level}</span>
                </div>
                <div className="col-span-3 text-right text-sm font-bold text-[#FF8A3D]">{points(entry).toLocaleString()}</div>
              </div>
            )
          })}
        </div>
      </QuestCard>
    </>
  )
}

function TeamsView({ entries }: { entries: LeaderboardEntry[] }) {
  const cohorts = useMemo(() => {
    const map = new Map<string, LeaderboardEntry[]>()
    for (const e of entries) {
      const list = map.get(e.level) || []
      list.push(e)
      map.set(e.level, list)
    }
    return LEVEL_ORDER.filter((l) => map.has(l)).map((level) => {
      const members = (map.get(level) || []).sort((a, b) => b.total_points - a.total_points)
      const totalPoints = members.reduce((s, m) => s + m.total_points, 0)
      return { level, members, totalPoints, avg: Math.round(totalPoints / members.length) }
    }).sort((a, b) => b.totalPoints - a.totalPoints)
  }, [entries])

  return (
    <>
      <p className="text-xs text-slate-500">Team standings grouped by level cohort — aggregate points across everyone at each mastery tier.</p>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {cohorts.map((c, i) => (
          <QuestCard key={c.level} title={`${c.level} Cohort`} eyebrow={`Team rank #${i + 1}`}>
            <div className="flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-white/[0.06] text-[#FF8A3D]">
                <Users className="h-6 w-6" />
              </div>
              <div>
                <p className="text-2xl font-bold text-white">{c.totalPoints.toLocaleString()}<span className="ml-1 text-sm font-medium text-slate-500">pts</span></p>
                <p className="text-xs text-slate-400">{c.members.length} member{c.members.length !== 1 ? 's' : ''} · avg {c.avg.toLocaleString()} pts</p>
              </div>
            </div>
            <div className="mt-4 space-y-2 border-t border-white/5 pt-4">
              {c.members.slice(0, 3).map((m, idx) => (
                <div key={m.user_id} className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2 truncate text-slate-300">
                    <span className="w-4 text-xs text-slate-500">{idx + 1}</span>
                    {m.display_name}
                  </span>
                  <span className="text-slate-400">{m.total_points.toLocaleString()}</span>
                </div>
              ))}
            </div>
          </QuestCard>
        ))}
      </div>
    </>
  )
}

function SwagPanel() {
  const tiers = [
    { icon: Crown, color: '#F5B72E', place: '1st Place', prize: 'Champion recognition' },
    { icon: Medal, color: '#CBD5E1', place: '2nd Place', prize: 'Weekly recognition' },
    { icon: Medal, color: '#B45309', place: '3rd Place', prize: 'Weekly recognition' },
  ]
  return (
    <QuestCard title="Weekly Recognition" eyebrow="Recognition">
      <p className="-mt-2 mb-4 text-sm text-slate-400">Top performers each week earn Databricks recognition.</p>
      <div className="grid gap-3 sm:grid-cols-3">
        {tiers.map((t) => (
          <div key={t.place} className="rounded-xl border border-white/10 bg-white/[0.03] p-4 text-center">
            <t.icon className="mx-auto h-6 w-6" style={{ color: t.color }} />
            <p className="mt-1 text-xs font-bold uppercase tracking-wide" style={{ color: t.color }}>{t.place}</p>
            <p className="mt-1 text-sm text-slate-300">{t.prize}</p>
          </div>
        ))}
      </div>
    </QuestCard>
  )
}

function LeaderboardSkeleton() {
  return (
    <>
      <div className="grid grid-cols-3 gap-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="quest-card p-5">
            <div className="relative z-10 flex flex-col items-center gap-3">
              <Skeleton className="h-14 w-14 rounded-full" />
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-5 w-16" />
            </div>
          </div>
        ))}
      </div>
      <div className="quest-card p-5">
        <div className="relative z-10 space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      </div>
    </>
  )
}
