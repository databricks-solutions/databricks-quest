import { useEffect, useState } from 'react'
import {
  Star, Flame, Award, Target, TrendingUp,
  Zap, Calendar, Search,
} from 'lucide-react'
import type { UserProfile, Mission, Notification } from '../types'

const STAT_ICONS = [
  { key: 'total_points', label: 'Total Points', icon: Star, color: 'text-amber-400', bg: 'from-amber-500/20 to-amber-600/10' },
  { key: 'current_streak', label: 'Day Streak', icon: Flame, color: 'text-orange-400', bg: 'from-orange-500/20 to-orange-600/10' },
  { key: 'missions_completed', label: 'Missions Done', icon: Target, color: 'text-cyan-400', bg: 'from-cyan-500/20 to-cyan-600/10' },
  { key: 'badge_count', label: 'Badges Earned', icon: Award, color: 'text-violet-400', bg: 'from-violet-500/20 to-violet-600/10' },
]

const LEVEL_COLORS: Record<string, string> = {
  Bronze: 'from-amber-800 to-amber-900',
  Silver: 'from-slate-500 to-slate-600',
  Gold: 'from-yellow-500 to-yellow-600',
  Platinum: 'from-violet-500 to-violet-600',
  Elite: 'from-orange-500 to-red-500',
}

const MISSION_ICONS: Record<string, typeof Star> = {
  rocket: Zap,
  briefcase: Target,
  'git-branch': TrendingUp,
  'play-circle': Zap,
  clock: Calendar,
  'upload-cloud': TrendingUp,
  'calendar-check': Calendar,
  search: Search,
}

interface Props {
  profile: UserProfile | null
  onRefresh: () => void
}

export default function Dashboard({ profile }: Props) {
  const [missions, setMissions] = useState<Mission[]>([])
  const [recentNotifs, setRecentNotifs] = useState<Notification[]>([])

  useEffect(() => {
    fetch('/api/missions').then(r => r.json()).then(d => setMissions(d.missions || [])).catch(() => {})
    fetch('/api/notifications').then(r => r.json()).then(d => setRecentNotifs((d.notifications || []).slice(0, 5))).catch(() => {})
  }, [])

  if (!profile) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="w-12 h-12 border-4 border-amber-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-slate-400">Loading your quest data...</p>
        </div>
      </div>
    )
  }

  if (profile.setup_required) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="card p-8 max-w-md text-center">
          <div className="w-16 h-16 rounded-full bg-amber-500/20 flex items-center justify-center mx-auto mb-4">
            <Zap className="w-8 h-8 text-amber-400" />
          </div>
          <h2 className="text-xl font-bold mb-2">Setup Required</h2>
          <p className="text-slate-400 text-sm">
            The Quest scoring pipeline hasn't run yet. An admin needs to run the scoring notebook
            to populate data from system tables.
          </p>
        </div>
      </div>
    )
  }

  const completedMissions = missions.filter(m => m.status === 'completed')
  const availableMissions = missions.filter(m => m.status === 'available').slice(0, 3)
  const level = profile.level || 'Bronze'

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Level banner */}
      <div className={`card p-6 bg-gradient-to-r ${LEVEL_COLORS[level]} relative overflow-hidden`}>
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_80%_50%,rgba(255,255,255,0.08),transparent)]" />
        <div className="relative flex items-center justify-between">
          <div>
            <p className="text-sm text-white/70 font-medium">Current Level</p>
            <h2 className="text-3xl font-bold text-white mt-1">{level}</h2>
            <div className="flex items-center gap-3 mt-3">
              <div className="flex-1 h-2 bg-black/30 rounded-full w-48 overflow-hidden">
                <div
                  className="h-full bg-white/80 rounded-full progress-bar-animated"
                  style={{ width: `${profile.level_progress?.progress_pct || 0}%` }}
                />
              </div>
              <span className="text-xs text-white/70">
                {profile.total_points} / {profile.level_progress?.level_ceiling} pts
              </span>
            </div>
          </div>
          <div className="text-right">
            <p className="text-4xl font-bold text-white">{profile.total_points}</p>
            <p className="text-sm text-white/60">total points</p>
          </div>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {STAT_ICONS.map(stat => {
          const Icon = stat.icon
          const value = (profile as unknown as Record<string, unknown>)[stat.key] as number
          return (
            <div key={stat.key} className={`card p-4 bg-gradient-to-br ${stat.bg}`}>
              <div className="flex items-center justify-between">
                <Icon className={`w-5 h-5 ${stat.color}`} />
              </div>
              <p className="text-2xl font-bold text-white mt-3">{value ?? 0}</p>
              <p className="text-xs text-slate-400 mt-1">{stat.label}</p>
            </div>
          )
        })}
      </div>

      <div className="grid lg:grid-cols-5 gap-6">
        {/* Next missions */}
        <div className="lg:col-span-3">
          <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Next Missions</h3>
          {availableMissions.length === 0 ? (
            <div className="card p-6 text-center text-slate-500">
              All missions completed! You're a Databricks champion.
            </div>
          ) : (
            <div className="space-y-3">
              {availableMissions.map(m => {
                const Icon = MISSION_ICONS[m.icon] || Target
                return (
                  <div key={m.id} className="card-hover p-4 flex items-center gap-4">
                    <div className="w-10 h-10 rounded-lg bg-slate-800 flex items-center justify-center shrink-0">
                      <Icon className="w-5 h-5 text-amber-400" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-white text-sm">{m.name}</p>
                      <p className="text-xs text-slate-400 mt-0.5">{m.description}</p>
                    </div>
                    <div className="text-right shrink-0">
                      <span className="text-amber-400 font-bold text-sm">+{m.points}</span>
                      <p className="text-xs text-slate-500">pts</p>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Recent activity */}
        <div className="lg:col-span-2">
          <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Recent Activity</h3>
          <div className="card p-0 divide-y divide-slate-800">
            {recentNotifs.length === 0 && completedMissions.length === 0 ? (
              <div className="p-4 text-sm text-slate-500">No activity yet. Start using Databricks to earn points!</div>
            ) : (
              recentNotifs.map((n, i) => (
                <div key={i} className="p-3 hover:bg-slate-800/50 transition">
                  <p className="text-sm text-white font-medium">{n.title}</p>
                  <div className="flex items-center justify-between mt-1">
                    <p className="text-xs text-slate-400">{n.message}</p>
                  </div>
                  {n.points > 0 && (
                    <span className="text-xs text-amber-400 font-semibold">+{n.points} pts</span>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Badges */}
      {profile.badges && profile.badges.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Your Badges</h3>
          <div className="flex gap-4 flex-wrap">
            {profile.badges.map(badge => (
              <div
                key={badge.badge_id}
                className="card p-4 flex flex-col items-center gap-2 w-32 badge-glow"
              >
                <div className="w-12 h-12 rounded-full bg-gradient-to-br from-amber-500 to-orange-600 flex items-center justify-center">
                  <Award className="w-6 h-6 text-white" />
                </div>
                <p className="text-xs font-semibold text-white text-center">{badge.badge_name}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
