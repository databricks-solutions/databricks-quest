import { useMemo } from 'react'
import {
  Activity,
  ArrowUpRight,
  Award,
  BarChart3,
  CheckCircle2,
  ChevronRight,
  Database,
  Flame,
  GitBranch,
  Lock,
  Medal,
  Play,
  RefreshCw,
  Shield,
  Star,
  Target,
  Trophy,
} from 'lucide-react'
import { LevelHero } from './quest/LevelHero'
import { MomentumCard } from './quest/MomentumCard'
import { QuestCard } from './quest/QuestCard'
import { QuestBadge } from './quest/QuestBadge'
import { Reveal } from './quest/Motion'
import { useApi } from '../lib/api'
import { decorateBadges } from '../lib/badges'
import { levelInfo } from '../lib/levels'
import { difficultyForPoints } from '../lib/mission-meta'
import type { Mission, Notification, UserProfile, LeaderboardEntry } from '../types'

type DashboardV2Props = {
  profile: UserProfile | null
  notifications: Notification[]
  onRefresh: () => void
  onNavigate?: (page: string) => void
}

type PathItem = { label: string; done: boolean }

export default function DashboardV2({ profile, notifications, onRefresh, onNavigate }: DashboardV2Props) {
  const missionsApi = useApi<{ missions: Mission[] }>('/api/missions')
  const leaderboardApi = useApi<{ leaderboard: LeaderboardEntry[] }>('/api/leaderboard?period=all')

  const missions = missionsApi.data?.missions ?? []
  const leaderboard = leaderboardApi.data?.leaderboard ?? []

  const totalPoints = profile?.total_points ?? 0
  const level = profile?.level ?? 'Bronze'
  const missionsCompleted = profile?.missions_completed ?? missions.filter((m) => m.status === 'completed').length
  const badgeCount = profile?.badge_count ?? profile?.badges?.length ?? 0
  const streak = profile?.current_streak ?? 0
  const breadth = profile?.distinct_products_used ?? 0

  const info = levelInfo(totalPoints)
  const nextLevel = info.next?.name ?? 'Max'
  const pointsToNext = profile?.level_progress
    ? Math.max(profile.level_progress.level_ceiling - totalPoints, 0)
    : info.pointsToNext
  const progressPct = profile?.level_progress?.progress_pct ?? info.progressPct

  const you = useMemo(
    () =>
      leaderboard.find(
        (e) => !!profile && (e.user_id === profile.user_id || e.display_name === profile.display_name),
      ),
    [leaderboard, profile],
  )
  const weeklyDelta = you?.weekly_points ?? 0
  const teamRank = you?.all_time_rank

  const nextMissions = useMemo(() => missions.filter((m) => m.status !== 'completed'), [missions])
  const recommended = nextMissions[0]

  const pathFor = (category: string, n: number): PathItem[] =>
    missions
      .filter((m) => m.category === category)
      .slice(0, n)
      .map((m) => ({
        label: m.status === 'completed' ? m.name : `${m.name}  +${m.points} pts`,
        done: m.status === 'completed',
      }))

  const foundationItems = pathFor('Getting Started', 2)
  const deItems = pathFor('Data Engineering', 2)
  const aimlItems = pathFor('AI / ML', 1)
  const aimlLocked = aimlItems.length > 0 && aimlItems.every((i) => !i.done)

  const recent = notifications.slice(0, 3)

  const badgePreview = useMemo(() => decorateBadges(profile).slice(0, 4), [profile])

  const aboveYou = teamRank ? leaderboard.find((e) => e.all_time_rank === teamRank - 1) : undefined
  const miniDelta =
    !you || !teamRank
      ? null
      : teamRank === 1
        ? "You're leading the board"
        : aboveYou
          ? `↑ ${(aboveYou.total_points - you.total_points).toLocaleString()} pts to reach #${teamRank - 1}`
          : null

  return (
    <div className="mx-auto max-w-[1720px] space-y-3">
      <LevelHero
        level={`${level} Level`}
        points={totalPoints}
        nextLevel={nextLevel}
        pointsToNext={pointsToNext}
        progressPct={progressPct}
        weeklyDelta={weeklyDelta}
        missionsCompleted={missionsCompleted}
        badgesUnlocked={badgeCount}
        rankLabel={teamRank ? `Rank #${teamRank} all-time` : undefined}
      />

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Reveal index={0}><MomentumCard icon={ArrowUpRight} label="Weekly Momentum" value={`+${weeklyDelta.toLocaleString()} pts`} detail={weeklyDelta > 0 ? 'earned so far this week' : 'No points yet this week'} accent="#FF5F1F" /></Reveal>
        <Reveal index={1}><MomentumCard icon={Database} label="Platform Breadth" value={`${breadth} / 6`} detail="product areas explored" accent="#00C2D7" /></Reveal>
        <Reveal index={2}><MomentumCard icon={Flame} label="Streak" value={`${streak} ${streak === 1 ? 'day' : 'days'}`} detail="consecutive active days" accent="#FF8A3D" /></Reveal>
        <Reveal index={3}><MomentumCard icon={Trophy} label="Team Rank" value={teamRank ? `#${teamRank}` : '—'} detail={teamRank ? 'all-time standing' : 'unranked so far'} accent="#8B5CF6" /></Reveal>
      </div>

      <div className="grid gap-3 xl:grid-cols-[360px_minmax(0,1fr)_400px]">
        <div className="space-y-3">
          <QuestCard title="Quest Path" action={<span className="text-xs text-slate-500">By category</span>}>
            <div className="space-y-4">
              <PathSection title="Foundation" color="#22C55E" items={foundationItems} />
              <PathSection title="Data Engineering" color="#FF5F1F" items={deItems} />
              <PathSection title="AI & ML" color="#8B5CF6" items={aimlItems} locked={aimlLocked} />
            </div>
          </QuestCard>

          <QuestCard title="Badge Vault" action={<span className="text-xs text-slate-500">{badgeCount} earned</span>}>
            <div className="grid grid-cols-4 gap-2">
              {badgePreview.map((b) => (
                <QuestBadge key={b.id} name={b.name} imageSrc={b.image} locked={!b.earned} />
              ))}
            </div>
          </QuestCard>
        </div>

        <QuestCard title="Recommended Next Mission" eyebrow="Recommended quest" className="overflow-hidden">
          {recommended ? (
            <div className="relative h-full overflow-hidden rounded-2xl border border-[#FF5F1F]/25 bg-gradient-to-br from-[#2B160F] via-[#151827] to-[#101827] p-5">
              <div className="absolute inset-0 bg-[url('/assets/backgrounds/quest-topography.svg')] bg-cover bg-center opacity-25" />
              <div className="relative z-10 max-w-xl">
                <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-[#F5B72E]">Recommended Quest</p>
                <h2 className="mt-2 text-2xl font-bold tracking-tight text-white">{recommended.name}</h2>
                <p className="mt-1.5 max-w-lg text-sm leading-5 text-slate-200">{recommended.description}</p>
                <div className="mt-4 text-4xl font-bold text-[#FF8A3D]">+{recommended.points}<span className="ml-2 text-xl font-medium">pts</span></div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Chip icon={GitBranch} label={recommended.category} />
                  <Chip icon={BarChart3} label={difficultyForPoints(recommended.points)} />
                  <Chip icon={Shield} label="Detected from system tables" />
                </div>
                <button
                  onClick={() => onNavigate?.('missions')}
                  className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[#FF7A1A] to-[#E93D1E] px-5 py-3 text-sm font-semibold text-white shadow-xl shadow-[#FF5F1F]/20 transition hover:brightness-110"
                >
                  Start Mission <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          ) : (
            <div className="flex h-full min-h-[200px] flex-col items-center justify-center rounded-2xl border border-white/10 bg-white/[0.02] p-6 text-center">
              <CheckCircle2 className="h-8 w-8 text-[#22C55E]" />
              <p className="mt-3 text-sm font-semibold text-white">
                {missionsApi.loading ? 'Loading missions…' : 'All available missions complete'}
              </p>
              <p className="mt-1 text-xs text-slate-400">
                {missionsApi.loading ? 'Fetching your quest path.' : 'New quests appear as the scoring pipeline detects fresh activity.'}
              </p>
            </div>
          )}
        </QuestCard>

        <div className="space-y-3">
          <QuestCard title="Recent Activity" action={<span className="text-xs text-slate-500">Latest</span>}>
            {recent.length > 0 ? (
              <div className="space-y-1.5">
                {recent.map((n, i) => (
                  <div key={`${n.title}-${i}`} className="flex items-center gap-3 rounded-xl p-2 transition hover:bg-white/[0.035]">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white/[0.05] text-[#FF8A3D]">
                      {n.notification_type === 'badge' ? <Award className="h-4 w-4" /> : n.notification_type === 'mission' ? <CheckCircle2 className="h-4 w-4 text-[#22C55E]" /> : <Activity className="h-4 w-4 text-[#00C2D7]" />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-white">{n.title}</p>
                      <p className="truncate text-xs text-slate-400">{n.message}</p>
                    </div>
                    {n.points > 0 && <span className="text-xs font-semibold text-[#F5B72E]">+{n.points} pts</span>}
                  </div>
                ))}
              </div>
            ) : (
              <p className="py-4 text-center text-xs text-slate-500">No recent activity yet — complete a platform action to get started.</p>
            )}
          </QuestCard>

          <QuestCard title="Mini Leaderboard" action={<span className="text-xs text-slate-500">All time</span>}>
            {leaderboard.length > 0 ? (
              <>
                <div className="space-y-1.5">
                  {leaderboard.slice(0, 4).map((entry, index) => {
                    const isYou = !!profile && (entry.user_id === profile.user_id || entry.display_name === profile.display_name)
                    return (
                      <div key={entry.user_id || entry.display_name} className={`flex items-center gap-3 rounded-xl border px-3 py-2 ${isYou ? 'border-[#FF5F1F]/45 bg-[#FF5F1F]/8' : 'border-transparent hover:bg-white/[0.035]'}`}>
                        <span className="w-5 text-xs font-semibold text-slate-400">{entry.all_time_rank || index + 1}</span>
                        <Medal className={`h-4 w-4 ${(entry.all_time_rank || index + 1) === 1 ? 'text-[#F5B72E]' : 'text-slate-500'}`} />
                        <span className={`min-w-0 flex-1 truncate text-sm ${isYou ? 'font-semibold text-[#FF8A3D]' : 'text-slate-200'}`}>{entry.display_name}{isYou ? ' (You)' : ''}</span>
                        <span className="text-sm text-slate-300">{entry.total_points.toLocaleString()} pts</span>
                      </div>
                    )
                  })}
                </div>
                {miniDelta && <p className="mt-3 text-xs text-slate-500">{miniDelta}</p>}
              </>
            ) : (
              <p className="py-4 text-center text-xs text-slate-500">
                {leaderboardApi.loading ? 'Loading rankings…' : 'Rankings appear after the first scoring run.'}
              </p>
            )}
          </QuestCard>
        </div>
      </div>

      <button
        onClick={onRefresh}
        className="fixed bottom-5 right-5 flex items-center gap-2 rounded-full border border-white/10 bg-[#111827]/90 px-4 py-2 text-xs font-medium text-slate-300 shadow-2xl backdrop-blur hover:bg-white/[0.06]"
      >
        <RefreshCw className="h-3.5 w-3.5" /> Refresh
      </button>
    </div>
  )
}

function Chip({ icon: Icon, label }: { icon: typeof Target; label: string }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-xs text-slate-300">
      <Icon className="h-3.5 w-3.5" /> {label}
    </span>
  )
}

function PathSection({
  title,
  color,
  items,
  locked = false,
}: {
  title: string
  color: string
  items: PathItem[]
  locked?: boolean
}) {
  const complete = items.length > 0 && items.every((i) => i.done)
  const currentIndex = items.findIndex((i) => !i.done)

  return (
    <div className="relative pl-8">
      <div className="absolute bottom-0 left-[10px] top-2 w-px bg-white/10" />
      <div className="absolute left-0 top-0 flex h-5 w-5 items-center justify-center rounded-full border bg-[#111827]" style={{ borderColor: color, color }}>
        {locked ? <Lock className="h-3 w-3" /> : complete ? <CheckCircle2 className="h-3 w-3" /> : <Star className="h-3 w-3" />}
      </div>
      <h4 className="text-xs font-bold uppercase tracking-[0.14em]" style={{ color }}>{title}</h4>
      <div className="mt-2.5 space-y-2">
        {items.length === 0 ? (
          <p className="text-xs text-slate-600">No missions in this track yet</p>
        ) : (
          items.map((item, i) => (
            <div key={item.label} className={`flex items-center gap-3 rounded-xl border px-3 py-2 text-sm ${!locked && !item.done && currentIndex === i ? 'border-[#FF5F1F]/30 bg-[#FF5F1F]/8 text-white' : 'border-transparent text-slate-400'}`}>
              {item.done ? <CheckCircle2 className="h-4 w-4 text-[#22C55E]" /> : locked ? <Lock className="h-4 w-4 text-slate-600" /> : currentIndex === i ? <Play className="h-4 w-4 text-[#FF8A3D]" /> : <span className="h-4 w-4 rounded-full border border-white/15" />}
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
