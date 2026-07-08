import { Crown, Lock, Medal, Sparkles, TrendingUp, Trophy } from 'lucide-react'
import type { EventLeaderboard as EventLeaderboardData, LeaderboardRow, RecentScore } from '../types'
import { useApi } from '../lib/api'
import { QuestCard } from './quest/QuestCard'
import { EmptyState, ErrorState, Skeleton } from './quest/States'

const PODIUM = [
  { ring: 'border-[#F5B72E]/40 bg-[#F5B72E]/[0.08]', text: 'text-[#F5B72E]', Icon: Crown, label: '1st' },
  { ring: 'border-slate-300/30 bg-slate-300/[0.06]', text: 'text-slate-200', Icon: Medal, label: '2nd' },
  { ring: 'border-[#CD7F32]/40 bg-[#CD7F32]/[0.08]', text: 'text-[#D08B4B]', Icon: Medal, label: '3rd' },
]

function relativeTime(iso?: string | null): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (secs < 60) return `${secs}s ago`
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}

function Podium({ rows, youTeamId }: { rows: LeaderboardRow[]; youTeamId?: string | null }) {
  const top = rows.slice(0, 3)
  if (top.length === 0) return null
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {top.map((row, i) => {
        const p = PODIUM[i]
        const mine = row.team_id === youTeamId
        return (
          <div
            key={row.team_id}
            className={`relative overflow-hidden rounded-2xl border px-4 py-4 ${p.ring} ${mine ? 'ring-1 ring-[#FF5F1F]/50' : ''}`}
          >
            <div className="flex items-center justify-between">
              <span className={`inline-flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider ${p.text}`}>
                <p.Icon className="h-4 w-4" /> {p.label}
              </span>
              {mine && <span className="text-[10px] font-semibold uppercase tracking-wider text-[#FF8A3D]">You</span>}
            </div>
            <p className="mt-2 truncate text-[15px] font-semibold text-white">{row.display_name || row.team_id}</p>
            <p className={`mt-0.5 text-2xl font-bold ${p.text}`}>{row.total_points}</p>
            <p className="text-[11px] text-slate-400">points</p>
          </div>
        )
      })}
    </div>
  )
}

function StandingsTable({ rows, youTeamId }: { rows: LeaderboardRow[]; youTeamId?: string | null }) {
  return (
    <div className="overflow-hidden rounded-xl border border-white/10">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/10 bg-white/[0.02] text-left text-[11px] uppercase tracking-wider text-slate-500">
            <th className="px-3 py-2 font-semibold">Rank</th>
            <th className="px-3 py-2 font-semibold">Team</th>
            <th className="px-3 py-2 text-right font-semibold">Points</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const mine = row.team_id === youTeamId
            return (
              <tr
                key={row.team_id}
                className={`border-b border-white/5 last:border-0 ${mine ? 'bg-[#FF5F1F]/[0.07]' : ''}`}
              >
                <td className="px-3 py-2 font-mono text-slate-300">{row.rank ?? '—'}</td>
                <td className="px-3 py-2">
                  <span className="font-medium text-white">{row.display_name || row.team_id}</span>
                  {mine && <span className="ml-2 text-[10px] font-semibold uppercase tracking-wider text-[#FF8A3D]">You</span>}
                </td>
                <td className="px-3 py-2 text-right font-semibold text-[#FF8A3D]">{row.total_points}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function feedLabel(r: RecentScore): string {
  if (r.source_type === 'hint_penalty') return `revealed a hint${r.task_title ? ` on “${r.task_title}”` : ''}`
  if (r.source_type === 'manual_adjustment') return 'host adjustment'
  if (r.task_title) return `completed “${r.task_title}”`
  return r.reason || 'scored'
}

function ActivityFeed({ recent }: { recent: RecentScore[] }) {
  if (recent.length === 0) {
    return <p className="text-sm text-slate-400">No scoring activity yet — be the first to put points on the board.</p>
  }
  return (
    <ul className="space-y-1.5">
      {recent.map((r) => {
        const positive = r.points_delta >= 0
        return (
          <li key={r.scoring_event_id} className="flex items-center justify-between gap-3 text-sm">
            <span className="min-w-0 truncate text-slate-300">
              <span className="font-medium text-white">{r.team_name || 'Unassigned'}</span>{' '}
              <span className="text-slate-400">{feedLabel(r)}</span>
            </span>
            <span className="flex shrink-0 items-center gap-2">
              <span className={`font-semibold ${positive ? 'text-emerald-300' : 'text-[#FB7185]'}`}>
                {positive ? '+' : ''}{r.points_delta}
              </span>
              <span className="text-[11px] text-slate-500">{relativeTime(r.created_at)}</span>
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export default function EventLeaderboard({ eventRef }: { eventRef: string }) {
  // Live standings: poll every 10s (paused when the tab is hidden) on top of
  // the manual Refresh button.
  const { data, loading, loaded, error, reload } = useApi<EventLeaderboardData>(
    `/api/events/${eventRef}/leaderboard`,
    null,
    { pollMs: 10000 },
  )

  if (loading && !loaded) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }
  if (error && !data) return <ErrorState message={error} onRetry={reload} />
  if (!data) return null

  const rows = data.leaderboard ?? []
  const youTeamId = data.you?.team_id ?? null

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="inline-flex items-center gap-2 text-[15px] font-semibold text-white">
          <Trophy className="h-4 w-4 text-[#FF8A3D]" /> Live standings
        </h3>
        <div className="flex items-center gap-2">
          {data.frozen && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-sky-500/25 bg-sky-500/[0.08] px-3 py-1 text-xs text-sky-200">
              <Lock className="h-3.5 w-3.5" /> {data.status === 'completed' ? 'Final results' : 'Scoring frozen'}
            </span>
          )}
          <button
            onClick={reload}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-xs text-slate-300 transition hover:bg-white/[0.07]"
          >
            <TrendingUp className="h-3.5 w-3.5" /> Refresh
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          icon={Sparkles}
          title="No scores yet"
          message="Once teams start passing tasks, the leaderboard will light up here."
        />
      ) : (
        <>
          <Podium rows={rows} youTeamId={youTeamId} />
          {data.you && data.you.rank == null && (
            <p className="text-xs text-slate-400">
              Your team <span className="font-semibold text-white">{data.you.display_name}</span> hasn’t scored yet — pass a task to get on the board.
            </p>
          )}
          <QuestCard title="All teams">
            <StandingsTable rows={rows} youTeamId={youTeamId} />
          </QuestCard>
        </>
      )}

      <QuestCard title="Recent activity">
        <ActivityFeed recent={data.recent ?? []} />
      </QuestCard>
    </div>
  )
}
