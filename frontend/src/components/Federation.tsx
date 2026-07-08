import { useMemo, useState } from 'react'
import {
  Crown,
  Medal,
  Trophy,
  Users,
  Wifi,
  WifiOff,
  Upload,
  Server,
  UserX,
  CheckCircle2,
} from 'lucide-react'
import type {
  FederationStatus,
  FederationLeaderboard,
  EventWorkspace,
  UnmappedIdentity,
} from '../types'
import { useApi } from '../lib/api'
import { QuestCard } from './quest/QuestCard'
import { EmptyState, ErrorState, Skeleton } from './quest/States'

function RankIcon({ rank }: { rank: number | null }) {
  if (rank === 1) return <Crown className="h-5 w-5 text-[#F5B72E]" />
  if (rank === 2) return <Medal className="h-5 w-5 text-slate-300" />
  if (rank === 3) return <Medal className="h-5 w-5 text-amber-600" />
  return <span className="w-5 text-center font-mono text-sm text-slate-500">{rank ?? '—'}</span>
}

function DbHealth({ connected }: { connected?: boolean }) {
  const ok = connected !== false
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${
        ok
          ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
          : 'border-[#F43F5E]/30 bg-[#F43F5E]/10 text-[#FB7185]'
      }`}
      title={ok ? 'Connected to the shared event database' : 'Cannot reach the shared event database'}
    >
      {ok ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
      {ok ? 'Live' : 'Offline'}
    </span>
  )
}

export default function Federation({ status }: { status: FederationStatus }) {
  if (status.role === 'master') return <MasterConsole status={status} />
  return <ChildEventView status={status} />
}

// ── Child: overall leaderboard + your team's rank ────────────────────────────

export function ChildEventView({ status }: { status: FederationStatus }) {
  const { data, loading, loaded, error, reload } = useApi<FederationLeaderboard>(
    '/api/federation/leaderboard',
  )
  const rows = useMemo(
    () => [...(data?.leaderboard ?? [])].sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9)),
    [data],
  )
  const you = data?.you ?? null
  const mapped = data?.mapped ?? status.mapped
  const showError = loaded && error && rows.length === 0

  return (
    <div className="mx-auto max-w-[1100px] space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-slate-300">
            Event: <span className="font-semibold text-white">{status.event_slug || '—'}</span>
          </span>
          {status.workspace_id && (
            <span className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-slate-400">
              Workspace <span className="font-mono text-slate-300">{status.workspace_id}</span>
            </span>
          )}
        </div>
        <DbHealth connected={status.db_connected} />
      </div>

      {!mapped && (
        <div className="rounded-2xl border border-amber-500/25 bg-amber-500/[0.07] px-5 py-4 text-sm text-amber-200">
          <p className="font-semibold">You’re not on a team yet</p>
          <p className="mt-1 text-amber-200/80">
            Your workspace hasn’t been mapped to a team by the host. You can keep playing —
            your points are saved and will appear on the leaderboard as soon as the host imports
            the roster. (Ask your host to add{' '}
            <span className="font-mono">{status.submitted_by || 'your lab user'}</span>.)
          </p>
        </div>
      )}

      {mapped && you && (
        <div className="rounded-xl border border-[#FF5F1F]/30 bg-[#FF5F1F]/[0.08] px-5 py-3 text-sm">
          <span className="text-slate-400">Your team</span>
          <span className="ml-2 font-semibold text-white">{you.display_name || status.team?.display_name || 'Your team'}</span>
          <span className="ml-3 text-slate-400">rank</span>
          <span className="ml-2 font-bold text-[#FF8A3D]">{you.rank ? `#${you.rank}` : 'unranked'}</span>
          <span className="ml-3 text-slate-300">· {you.total_points.toLocaleString()} pts</span>
        </div>
      )}

      {showError ? (
        <ErrorState message={error ?? undefined} onRetry={reload} />
      ) : loading && rows.length === 0 ? (
        <LeaderboardSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Trophy}
          title="No scores yet"
          message="As teams complete tasks across every workspace, the event-wide leaderboard fills in here."
        />
      ) : (
        <QuestCard className="p-0">
          <div className="relative z-10 grid grid-cols-12 gap-2 border-b border-white/10 px-5 py-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            <div className="col-span-2">Rank</div>
            <div className="col-span-7">Team</div>
            <div className="col-span-3 text-right">Points</div>
          </div>
          <div className="relative z-10 divide-y divide-white/5">
            {rows.map((entry) => {
              const isYou = !!you && entry.team_id === you.team_id
              return (
                <div
                  key={entry.team_id}
                  className={`grid grid-cols-12 items-center gap-2 px-5 py-3.5 transition ${
                    isYou ? 'bg-[#FF5F1F]/[0.10] ring-1 ring-inset ring-[#FF5F1F]/30' : 'hover:bg-white/[0.03]'
                  }`}
                >
                  <div className="col-span-2 flex items-center">
                    <RankIcon rank={entry.rank} />
                  </div>
                  <div className="col-span-7 flex items-center gap-3">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/[0.06] text-xs font-bold text-[#FF8A3D]">
                      {(entry.display_name || '?')[0].toUpperCase()}
                    </div>
                    <span className={`truncate text-sm ${isYou ? 'font-semibold text-[#FF8A3D]' : 'text-slate-200'}`}>
                      {entry.display_name || entry.team_id}
                      {isYou ? ' (Your team)' : ''}
                    </span>
                  </div>
                  <div className="col-span-3 text-right text-sm font-bold text-[#FF8A3D]">
                    {entry.total_points.toLocaleString()}
                  </div>
                </div>
              )
            })}
          </div>
        </QuestCard>
      )}
    </div>
  )
}

// ── Master: workspace health + roster import + unmapped identities ───────────

function MasterConsole({ status }: { status: FederationStatus }) {
  const eventId = status.event_id
  return (
    <div className="mx-auto max-w-[1100px] space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-slate-300">
          Event: <span className="font-semibold text-white">{status.event_slug || '—'}</span>
        </span>
        <DbHealth connected={status.db_connected} />
      </div>
      {!eventId ? (
        <EmptyState
          icon={Server}
          title="No event resolved"
          message="Set QUEST_EVENT_SLUG (via deploy --event) to a created event so the host console can load workspace health and the roster."
        />
      ) : (
        <>
          <RosterImport eventId={eventId} />
          <WorkspacesHealth eventId={eventId} />
          <UnmappedIdentities eventId={eventId} />
        </>
      )}
    </div>
  )
}

export function RosterImport({ eventId }: { eventId: string }) {
  const [csv, setCsv] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setResult(null)
    setErr(null)
    try {
      const res = await fetch(`/api/host/events/${eventId}/roster/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ csv }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data?.error?.message || `Import failed (${res.status})`)
      setResult(
        `Imported ${data.rows} rows · ${data.teams_created} new teams · ` +
          `${data.participants_created} new participants · ${data.identities_mapped} mapped`,
      )
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Import failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <QuestCard title="Roster import" eyebrow="Identity">
      <p className="-mt-2 mb-3 text-sm text-slate-400">
        Paste a CSV mapping each lab workspace/user to a real person and team. Columns:
        <span className="font-mono text-slate-300"> workspace_id</span> (or workspace_host),
        <span className="font-mono text-slate-300"> lab_user_email</span>,
        <span className="font-mono text-slate-300"> team_name</span>, optional
        <span className="font-mono text-slate-300"> display_name</span>,
        <span className="font-mono text-slate-300"> real_email</span>. Re-import is idempotent.
      </p>
      <textarea
        value={csv}
        onChange={(e) => setCsv(e.target.value)}
        rows={7}
        spellCheck={false}
        placeholder={
          'workspace_id,lab_user_email,display_name,real_email,team_name\n' +
          'ws-anzgt-01,labuser+1@awsbricks.com,Ada Lovelace,ada@corp.com,Red Team'
        }
        className="w-full resize-y rounded-xl border border-white/10 bg-[#0B0F18] p-3 font-mono text-xs text-slate-200 outline-none focus:border-[#FF5F1F]/40"
      />
      <div className="mt-3 flex items-center gap-3">
        <button
          onClick={submit}
          disabled={busy || !csv.trim()}
          className="inline-flex items-center gap-2 rounded-xl border border-[#FF5F1F]/35 bg-[#FF5F1F]/15 px-4 py-2 text-sm font-medium text-white transition hover:bg-[#FF5F1F]/25 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Upload className="h-4 w-4" /> {busy ? 'Importing…' : 'Import roster'}
        </button>
        {result && (
          <span className="inline-flex items-center gap-1.5 text-sm text-emerald-300">
            <CheckCircle2 className="h-4 w-4" /> {result}
          </span>
        )}
        {err && <span className="text-sm text-[#FB7185]">{err}</span>}
      </div>
    </QuestCard>
  )
}

export function WorkspacesHealth({ eventId }: { eventId: string }) {
  const { data, loading, loaded, error, reload } = useApi<{ workspaces: EventWorkspace[] }>(
    `/api/host/events/${eventId}/workspaces`,
  )
  const rows = data?.workspaces ?? []
  const showError = loaded && error && rows.length === 0

  return (
    <QuestCard title="Workspaces" eyebrow="Health" className="p-0">
      <div className="px-5 pb-4">
        {showError ? (
          <ErrorState message={error ?? undefined} onRetry={reload} />
        ) : loading && rows.length === 0 ? (
          <div className="space-y-2 pt-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Server}
            title="No workspaces checked in yet"
            message="Child apps register themselves on startup. They appear here once deployed and pointed at this master."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                  <th className="py-2 pr-3">Workspace</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3 text-right">Scores</th>
                  <th className="py-2 pr-3 text-right">Points</th>
                  <th className="py-2 pr-3 text-right">Validations</th>
                  <th className="py-2 text-right">Last seen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {rows.map((w) => {
                  const stale = w.last_seen_at
                    ? Date.now() - new Date(w.last_seen_at).getTime() > 10 * 60 * 1000
                    : true
                  return (
                    <tr key={w.workspace_id} className="hover:bg-white/[0.03]">
                      <td className="py-2.5 pr-3">
                        <span className="font-mono text-xs text-slate-200">{w.workspace_id}</span>
                      </td>
                      <td className="py-2.5 pr-3">
                        <span
                          className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs ${
                            stale ? 'bg-slate-600/30 text-slate-400' : 'bg-emerald-500/15 text-emerald-300'
                          }`}
                        >
                          {stale ? 'idle' : 'active'}
                        </span>
                      </td>
                      <td className="py-2.5 pr-3 text-right text-slate-300">{w.scoring_events}</td>
                      <td className="py-2.5 pr-3 text-right font-semibold text-[#FF8A3D]">{w.points.toLocaleString()}</td>
                      <td className="py-2.5 pr-3 text-right text-slate-300">
                        {w.validation_passes}/{w.validations}
                      </td>
                      <td className="py-2.5 text-right text-xs text-slate-500">
                        {w.last_seen_at ? new Date(w.last_seen_at).toLocaleString() : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </QuestCard>
  )
}

export function UnmappedIdentities({ eventId }: { eventId: string }) {
  const { data, loading, loaded, error, reload } = useApi<{ unmapped: UnmappedIdentity[] }>(
    `/api/host/events/${eventId}/identities/unmapped`,
  )
  const rows = data?.unmapped ?? []
  const showError = loaded && error && rows.length === 0

  return (
    <QuestCard title="Unmapped identities" eyebrow="Reconciliation" className="p-0">
      <div className="px-5 pb-4">
        <p className="-mt-1 pb-3 text-sm text-slate-400">
          Workspaces writing scores that aren’t on the roster yet. Nothing is lost — add them to the
          roster CSV and re-import to attribute their points.
        </p>
        {showError ? (
          <ErrorState message={error ?? undefined} onRetry={reload} />
        ) : loading && rows.length === 0 ? (
          <div className="space-y-2">
            {Array.from({ length: 2 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="flex items-center gap-2 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.06] px-4 py-3 text-sm text-emerald-300">
            <CheckCircle2 className="h-4 w-4" /> Every workspace writing scores is mapped to a team.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                  <th className="py-2 pr-3">Workspace</th>
                  <th className="py-2 pr-3">Lab user</th>
                  <th className="py-2 pr-3 text-right">Scores</th>
                  <th className="py-2 text-right">Unattributed pts</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {rows.map((u) => (
                  <tr key={`${u.workspace_id}:${u.lab_user_email}`} className="hover:bg-white/[0.03]">
                    <td className="py-2.5 pr-3 font-mono text-xs text-slate-200">{u.workspace_id}</td>
                    <td className="py-2.5 pr-3">
                      <span className="inline-flex items-center gap-1.5 text-slate-300">
                        <UserX className="h-3.5 w-3.5 text-amber-400" /> {u.lab_user_email}
                      </span>
                    </td>
                    <td className="py-2.5 pr-3 text-right text-slate-300">{u.scoring_events}</td>
                    <td className="py-2.5 text-right font-semibold text-amber-300">
                      {u.unattributed_points?.toLocaleString?.() ?? u.unattributed_points}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </QuestCard>
  )
}

function LeaderboardSkeleton() {
  return (
    <div className="quest-card p-5">
      <div className="relative z-10 space-y-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    </div>
  )
}
