import { BarChart3, Crown, Download, FileText, Lightbulb, Trophy, Users } from 'lucide-react'
import { useApi } from '../lib/api'
import { QuestCard } from './quest/QuestCard'
import { EmptyState, Skeleton } from './quest/States'
import type { EventReport } from '../types'

/**
 * Host-only post-event report panel (PR11). Renders the structured report
 * inline (summary, champions, completion, blockers, follow-ups) and offers
 * JSON / CSV / Markdown downloads for account and enablement follow-up.
 */
export default function ReportPanel({ eventRef }: { eventRef: string }) {
  const { data, loading } = useApi<EventReport>(`/api/host/events/${eventRef}/report`)

  const download = (format: 'json' | 'csv' | 'markdown') => {
    // Open the export endpoint directly; the Content-Disposition header makes
    // the browser save it with a sensible filename.
    window.open(`/api/host/events/${eventRef}/export?format=${format}`, '_blank')
  }

  return (
    <QuestCard
      eyebrow="Report"
      title="Post-event report"
      action={
        <div className="flex items-center gap-1.5">
          <DownloadBtn label="JSON" onClick={() => download('json')} />
          <DownloadBtn label="CSV" onClick={() => download('csv')} />
          <DownloadBtn label="Markdown" onClick={() => download('markdown')} />
        </div>
      }
    >
      {loading && !data ? (
        <Skeleton className="h-40 w-full" />
      ) : !data ? (
        <EmptyState
          icon={BarChart3}
          title="Report unavailable"
          message="No report data yet — run the event and earn some scores, then refresh."
        />
      ) : (
        <div className="space-y-5">
          <SummaryStrip report={data} />

          {data.champions.length > 0 && (
            <section>
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
                <Crown className="h-3.5 w-3.5 text-[#FF8A3D]" /> Champions
              </h4>
              <div className="flex flex-wrap gap-2">
                {data.champions.map((c) => (
                  <span key={c.team_id} className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-sm text-white">
                    <Trophy className="h-3.5 w-3.5 text-[#FF8A3D]" />#{c.rank} {c.team_name}
                    <span className="text-slate-400">· {c.total_points} pts</span>
                  </span>
                ))}
                {data.fastest_team && (
                  <span className="inline-flex items-center gap-1.5 rounded-lg border border-sky-500/30 bg-sky-500/10 px-2.5 py-1.5 text-sm text-sky-200">
                    Fastest · {data.fastest_team.team_name} ({data.fastest_team.first_solves} first solves)
                  </span>
                )}
              </div>
            </section>
          )}

          <section>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Quest completion</h4>
            <div className="overflow-hidden rounded-xl border border-white/10">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-white/10 bg-white/[0.02] text-left text-[11px] uppercase tracking-wider text-slate-500">
                    <th className="px-3 py-2 font-semibold">Team</th>
                    <th className="px-3 py-2 font-semibold">Completed</th>
                    <th className="px-3 py-2 font-semibold">%</th>
                  </tr>
                </thead>
                <tbody>
                  {data.completion_matrix.map((m) => (
                    <tr key={m.team_id} className="border-b border-white/5 last:border-0">
                      <td className="px-3 py-2 text-white">{m.team_name}</td>
                      <td className="px-3 py-2 text-slate-300">{m.completed_count}/{m.total_tasks}</td>
                      <td className="px-3 py-2 text-slate-300">{m.completion_pct}%</td>
                    </tr>
                  ))}
                  {data.completion_matrix.length === 0 && (
                    <tr><td colSpan={3} className="px-3 py-4 text-center text-slate-400">No teams yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          {data.roster && data.roster.length > 0 && (
            <section>
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
                <Users className="h-3.5 w-3.5 text-[#FF8A3D]" /> Participant roster ({data.roster.length})
              </h4>
              <div className="overflow-hidden rounded-xl border border-white/10">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-white/10 bg-white/[0.02] text-left text-[11px] uppercase tracking-wider text-slate-500">
                      <th className="px-3 py-2 font-semibold">Participant</th>
                      <th className="px-3 py-2 font-semibold">Role</th>
                      <th className="px-3 py-2 font-semibold">Team</th>
                      <th className="px-3 py-2 text-right font-semibold">Tasks passed</th>
                      <th className="px-3 py-2 text-right font-semibold">Attempts</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.roster.map((r) => (
                      <tr key={r.user_id} className="border-b border-white/5 last:border-0">
                        <td className="px-3 py-2 text-white">{r.display_name}</td>
                        <td className="px-3 py-2 text-slate-400">{r.role}</td>
                        <td className="px-3 py-2 text-slate-300">{r.team_name}</td>
                        <td className="px-3 py-2 text-right text-slate-300">{r.tasks_passed}</td>
                        <td className="px-3 py-2 text-right text-slate-400">{r.attempts_total}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {data.blockers.length > 0 && (
            <section>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Blockers (hardest tasks)</h4>
              <ul className="space-y-1.5">
                {data.blockers.slice(0, 5).map((b) => (
                  <li key={b.task_id} className="flex items-center justify-between rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm">
                    <span className="text-white">{b.task_title} <span className="text-slate-500">· {b.quest_title}</span></span>
                    <span className="text-slate-400">{b.solved_teams}/{b.total_teams} solved · {b.failed_attempts} failed</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
              <Lightbulb className="h-3.5 w-3.5 text-[#FF8A3D]" /> Recommended follow-ups
            </h4>
            <ul className="space-y-1.5">
              {data.recommended_follow_ups.map((f, i) => (
                <li key={i} className="flex gap-2 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm text-slate-200">
                  <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" />
                  <span>{f}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </QuestCard>
  )
}

function SummaryStrip({ report }: { report: EventReport }) {
  const s = report.summary
  const tiles: { label: string; value: number | string }[] = [
    { label: 'Teams', value: s.teams },
    { label: 'Participants', value: s.participants },
    { label: 'Quests', value: s.quests },
    { label: 'Tasks', value: s.tasks },
    { label: 'Attempts', value: s.attempts },
    { label: 'Hint penalty', value: report.hint_total_penalty },
  ]
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      {tiles.map((t) => (
        <div key={t.label} className="rounded-xl border border-white/10 bg-white/[0.02] px-3 py-2 text-center">
          <div className="text-lg font-semibold text-white">{t.value}</div>
          <div className="text-[10px] uppercase tracking-wider text-slate-500">{t.label}</div>
        </div>
      ))}
    </div>
  )
}

function DownloadBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-xs text-slate-300 transition hover:bg-white/[0.07]"
    >
      <Download className="h-3.5 w-3.5" /> {label}
    </button>
  )
}
