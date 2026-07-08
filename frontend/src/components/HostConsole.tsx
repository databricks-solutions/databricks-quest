import { useCallback, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Crown,
  Flag,
  Loader2,
  Megaphone,
  Pause,
  Play,
  RefreshCw,
  Snowflake,
  SlidersHorizontal,
  Square,
  Trophy,
  Upload,
  Users,
} from 'lucide-react'
import { Boxes, Database, Trash2 } from 'lucide-react'
import type {
  FederationStatus,
  HostAttemptDetail,
  HostAttemptList,
  HostOverview,
  HostTeamRow,
  ResourceHealth,
  ResourcePlan,
} from '../types'
import { useApi } from '../lib/api'
import { QuestCard } from './quest/QuestCard'
import { EmptyState, ErrorState, Skeleton } from './quest/States'
import { ValidationStatus } from './EventPlay'
import ReportPanel from './ReportPanel'
import { RosterImport, WorkspacesHealth, UnmappedIdentities } from './Federation'

// Map a target lifecycle status to the host verb + button presentation.
const TRANSITIONS: Record<string, { verb: string; label: string; Icon: typeof Play; cls: string }> = {
  active: { verb: 'start', label: 'Start', Icon: Play, cls: 'border-emerald-500/40 bg-emerald-500/15 text-emerald-200' },
  paused: { verb: 'pause', label: 'Pause', Icon: Pause, cls: 'border-amber-500/40 bg-amber-500/15 text-amber-200' },
  frozen: { verb: 'freeze', label: 'Freeze', Icon: Snowflake, cls: 'border-sky-500/40 bg-sky-500/15 text-sky-200' },
  completed: { verb: 'complete', label: 'Complete', Icon: CheckCircle2, cls: 'border-violet-500/40 bg-violet-500/15 text-violet-200' },
  ready: { verb: 'ready', label: 'Mark ready', Icon: Flag, cls: 'border-slate-500/40 bg-slate-500/15 text-slate-200' },
  archived: { verb: 'archive', label: 'Archive', Icon: Square, cls: 'border-slate-600/40 bg-slate-600/15 text-slate-400' },
}

async function postJson(url: string, body?: unknown): Promise<any> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data?.error?.message || `Request failed (${res.status})`)
  return data
}

export default function HostConsole({
  eventRef,
  federation,
}: {
  eventRef: string
  // When present (federation `master` mount), the cross-workspace panels are
  // folded in below the standard host sections so master gets one console.
  federation?: FederationStatus
}) {
  const { data, loading, loaded, error, reload } = useApi<HostOverview>(`/api/host/events/${eventRef}`)

  if (loading && !data) return <Skeleton className="h-64 w-full" />
  if (loaded && error && !data) return <ErrorState message={error ?? undefined} onRetry={reload} />
  if (!data) return null

  const federationEventId = federation?.event_id || eventRef

  return (
    <div className="space-y-5">
      <LifecycleBar overview={data} eventRef={eventRef} onChange={reload} />
      <div className="grid gap-5 lg:grid-cols-2">
        <TeamsTable teams={data.teams} />
        <ScoreAdjuster eventRef={eventRef} teams={data.teams} onDone={reload} />
      </div>
      <AnnouncementComposer eventRef={eventRef} overview={data} onPosted={reload} />
      <ResourcesPanel eventRef={eventRef} />
      {federation && (
        <>
          <RosterImport eventId={federationEventId} />
          <WorkspacesHealth eventId={federationEventId} />
          <UnmappedIdentities eventId={federationEventId} />
        </>
      )}
      <ReportPanel eventRef={eventRef} />
      <AttemptsInspector eventRef={eventRef} />
      <PackImporter />
    </div>
  )
}

// ── Lifecycle controls ─────────────────────────────────────────────────────

function LifecycleBar({
  overview,
  eventRef,
  onChange,
}: {
  overview: HostOverview
  eventRef: string
  onChange: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const go = async (verb: string) => {
    setBusy(verb)
    setErr(null)
    try {
      await postJson(`/api/host/events/${eventRef}/${verb}`)
      onChange()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Transition failed')
    } finally {
      setBusy(null)
    }
  }

  const counts = overview.attempt_status_counts
  return (
    <QuestCard>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="text-sm text-slate-400">Status</span>
          <span className="rounded-full border border-[#FF5F1F]/30 bg-[#FF5F1F]/[0.08] px-3 py-1 text-sm font-semibold text-[#FF8A3D]">
            {overview.event.status}
          </span>
          {!overview.attempts_open && (
            <span className="text-xs text-amber-300">submissions closed</span>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {overview.allowed_transitions.length === 0 && (
            <span className="text-xs text-slate-500">No further transitions (terminal state)</span>
          )}
          {overview.allowed_transitions.map((target) => {
            const t = TRANSITIONS[target]
            if (!t) return null
            return (
              <button
                key={target}
                onClick={() => go(t.verb)}
                disabled={!!busy}
                className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-sm font-medium transition hover:brightness-110 disabled:opacity-50 ${t.cls}`}
              >
                {busy === t.verb ? <Loader2 className="h-4 w-4 animate-spin" /> : <t.Icon className="h-4 w-4" />}
                {t.label}
              </button>
            )
          })}
        </div>
      </div>
      {err && <p className="mt-2 text-sm text-[#FB7185]">{err}</p>}
      <div className="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-6">
        <Tile label="Players" value={overview.counts.participants} />
        <Tile label="Teams" value={overview.counts.teams} />
        <Tile label="Quests" value={overview.counts.quests} />
        <Tile label="Tasks" value={overview.counts.tasks} />
        <Tile label="Passed" value={counts.passed ?? 0} tone="emerald" />
        <Tile label="Failed" value={(counts.failed ?? 0) + (counts.error ?? 0)} tone="rose" />
      </div>
    </QuestCard>
  )
}

function Tile({ label, value, tone }: { label: string; value: number; tone?: 'emerald' | 'rose' }) {
  const color = tone === 'emerald' ? 'text-emerald-300' : tone === 'rose' ? 'text-[#FB7185]' : 'text-[#FF8A3D]'
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] py-2.5 text-center">
      <div className={`text-lg font-bold ${color}`}>{value}</div>
      <div className="text-[11px] uppercase tracking-wider text-slate-500">{label}</div>
    </div>
  )
}

// ── Teams & progress ─────────────────────────────────────────────────────────

function TeamsTable({ teams }: { teams: HostTeamRow[] }) {
  return (
    <QuestCard title="Teams & scores" eyebrow="Standings">
      {teams.length === 0 ? (
        <p className="text-sm text-slate-400">No teams created yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                <th className="py-2 pr-3">Rank</th>
                <th className="py-2 pr-3">Team</th>
                <th className="py-2 pr-3 text-right">Members</th>
                <th className="py-2 text-right">Points</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {teams.map((t) => (
                <tr key={t.team_id} className="hover:bg-white/[0.03]">
                  <td className="py-2.5 pr-3">
                    {t.rank === 1 ? (
                      <Crown className="h-4 w-4 text-[#F5B72E]" />
                    ) : (
                      <span className="font-mono text-slate-400">{t.rank ?? '—'}</span>
                    )}
                  </td>
                  <td className="py-2.5 pr-3">
                    <span className="flex items-center gap-2 text-slate-200">
                      <span className="h-2.5 w-2.5 rounded-full" style={{ background: t.color || '#FF5F1F' }} />
                      {t.display_name || t.name}
                    </span>
                  </td>
                  <td className="py-2.5 pr-3 text-right text-slate-300">{t.members}</td>
                  <td className="py-2.5 text-right font-semibold text-[#FF8A3D]">{t.score.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </QuestCard>
  )
}

// ── Manual score adjustment ────────────────────────────────────────────────

function ScoreAdjuster({
  eventRef,
  teams,
  onDone,
}: {
  eventRef: string
  teams: HostTeamRow[]
  onDone: () => void
}) {
  const [teamId, setTeamId] = useState('')
  const [points, setPoints] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    setMsg(null)
    setErr(null)
    const delta = parseInt(points, 10)
    if (!teamId) {
      setErr('Pick a team.')
      return
    }
    if (!delta) {
      setErr('Enter a non-zero point delta.')
      return
    }
    if (!reason.trim()) {
      setErr('A reason is required.')
      return
    }
    setBusy(true)
    try {
      await postJson(`/api/host/events/${eventRef}/adjustments`, {
        team_id: teamId,
        points_delta: delta,
        reason: reason.trim(),
      })
      setMsg(`Applied ${delta > 0 ? '+' : ''}${delta} pts`)
      setPoints('')
      setReason('')
      onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Adjustment failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <QuestCard title="Manual score adjustment" eyebrow="Scoring">
      <div className="space-y-3">
        <select
          value={teamId}
          onChange={(e) => setTeamId(e.target.value)}
          className="w-full rounded-xl border border-white/10 bg-[#0B0F18] px-3 py-2 text-sm text-slate-200 outline-none focus:border-[#FF5F1F]/40"
        >
          <option value="">Choose a team…</option>
          {teams.map((t) => (
            <option key={t.team_id} value={t.team_id}>
              {t.display_name || t.name} ({t.score} pts)
            </option>
          ))}
        </select>
        <input
          value={points}
          onChange={(e) => setPoints(e.target.value)}
          inputMode="numeric"
          placeholder="Points (e.g. 50 or -25)"
          className="w-full rounded-xl border border-white/10 bg-[#0B0F18] px-3 py-2 text-sm text-slate-200 outline-none focus:border-[#FF5F1F]/40"
        />
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason (required — written to the audit log)"
          className="w-full rounded-xl border border-white/10 bg-[#0B0F18] px-3 py-2 text-sm text-slate-200 outline-none focus:border-[#FF5F1F]/40"
        />
        <div className="flex items-center gap-3">
          <button
            onClick={submit}
            disabled={busy}
            className="inline-flex items-center gap-2 rounded-xl border border-[#FF5F1F]/35 bg-[#FF5F1F]/15 px-4 py-2 text-sm font-medium text-white transition hover:bg-[#FF5F1F]/25 disabled:opacity-50"
          >
            <SlidersHorizontal className="h-4 w-4" /> {busy ? 'Applying…' : 'Apply adjustment'}
          </button>
          {msg && (
            <span className="inline-flex items-center gap-1.5 text-sm text-emerald-300">
              <Trophy className="h-4 w-4" /> {msg}
            </span>
          )}
        </div>
        {err && <p className="text-sm text-[#FB7185]">{err}</p>}
      </div>
    </QuestCard>
  )
}

// ── Announcements ─────────────────────────────────────────────────────────

const SEVERITY_STYLE: Record<string, string> = {
  info: 'border-sky-500/25 bg-sky-500/[0.06] text-sky-200',
  warning: 'border-amber-500/25 bg-amber-500/[0.06] text-amber-200',
  critical: 'border-[#F43F5E]/25 bg-[#F43F5E]/[0.06] text-[#FB7185]',
}

function AnnouncementComposer({
  eventRef,
  overview,
  onPosted,
}: {
  eventRef: string
  overview: HostOverview
  onPosted: () => void
}) {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [severity, setSeverity] = useState('info')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const post = async () => {
    setBusy(true)
    setErr(null)
    try {
      await postJson(`/api/host/events/${eventRef}/announcements`, { title, body_md: body, severity })
      setTitle('')
      setBody('')
      onPosted()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to post')
    } finally {
      setBusy(false)
    }
  }

  return (
    <QuestCard title="Announcements" eyebrow="Broadcast">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-3">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Title"
            className="w-full rounded-xl border border-white/10 bg-[#0B0F18] px-3 py-2 text-sm text-slate-200 outline-none focus:border-[#FF5F1F]/40"
          />
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={3}
            placeholder="Message to all players…"
            className="w-full resize-y rounded-xl border border-white/10 bg-[#0B0F18] p-3 text-sm text-slate-200 outline-none focus:border-[#FF5F1F]/40"
          />
          <div className="flex items-center gap-3">
            <select
              value={severity}
              onChange={(e) => setSeverity(e.target.value)}
              className="rounded-xl border border-white/10 bg-[#0B0F18] px-3 py-2 text-sm text-slate-200 outline-none focus:border-[#FF5F1F]/40"
            >
              <option value="info">Info</option>
              <option value="warning">Warning</option>
              <option value="critical">Critical</option>
            </select>
            <button
              onClick={post}
              disabled={busy || !title.trim() || !body.trim()}
              className="inline-flex items-center gap-2 rounded-xl border border-[#FF5F1F]/35 bg-[#FF5F1F]/15 px-4 py-2 text-sm font-medium text-white transition hover:bg-[#FF5F1F]/25 disabled:opacity-50"
            >
              <Megaphone className="h-4 w-4" /> {busy ? 'Posting…' : 'Post'}
            </button>
          </div>
          {err && <p className="text-sm text-[#FB7185]">{err}</p>}
        </div>
        <div className="space-y-2">
          {overview.announcements.length === 0 ? (
            <p className="text-sm text-slate-400">No announcements yet.</p>
          ) : (
            overview.announcements.map((a) => (
              <div key={a.announcement_id} className={`rounded-xl border px-3 py-2 text-sm ${SEVERITY_STYLE[a.severity] ?? SEVERITY_STYLE.info}`}>
                <p className="font-semibold">{a.title}</p>
                <p className="mt-0.5 whitespace-pre-wrap opacity-90">{a.body_md}</p>
              </div>
            ))
          )}
        </div>
      </div>
    </QuestCard>
  )
}

// ── Attempts inspector (queue / results / failed) ─────────────────────────────

const STATUS_FILTERS = ['all', 'passed', 'failed', 'error', 'manual', 'running'] as const

function AttemptsInspector({ eventRef }: { eventRef: string }) {
  const [filter, setFilter] = useState<(typeof STATUS_FILTERS)[number]>('all')
  const qs = filter === 'all' ? '' : `?status=${filter}`
  const { data, loading, loaded, error, reload } = useApi<HostAttemptList>(
    `/api/host/events/${eventRef}/attempts${qs}`,
  )
  const attempts = data?.attempts ?? []
  const [openId, setOpenId] = useState<string | null>(null)

  return (
    <QuestCard
      title="Validation attempts"
      eyebrow="Inspector"
      action={
        <button onClick={reload} className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-xs text-slate-300 transition hover:bg-white/[0.07]">
          <RefreshCw className="h-3.5 w-3.5" /> Refresh
        </button>
      }
    >
      <div className="mb-3 flex flex-wrap gap-1.5">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded-full px-3 py-1 text-xs font-medium capitalize transition ${
              filter === f ? 'bg-[#FF5F1F]/20 text-[#FF8A3D]' : 'bg-white/[0.04] text-slate-400 hover:text-slate-200'
            }`}
          >
            {f}
            {data?.status_counts && f !== 'all' && typeof data.status_counts[f] === 'number'
              ? ` (${data.status_counts[f]})`
              : ''}
          </button>
        ))}
      </div>

      {loaded && error && attempts.length === 0 ? (
        <ErrorState message={error ?? undefined} onRetry={reload} />
      ) : loading && attempts.length === 0 ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : attempts.length === 0 ? (
        <EmptyState icon={Users} title="No attempts yet" message="Submissions from players will appear here as they come in." />
      ) : (
        <div className="divide-y divide-white/5">
          {attempts.map((a) => (
            <div key={a.attempt_id}>
              <button
                onClick={() => setOpenId(openId === a.attempt_id ? null : a.attempt_id)}
                className="flex w-full items-center justify-between gap-3 py-2.5 text-left hover:bg-white/[0.02]"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm text-slate-200">
                    {a.task_title || a.task_id}
                  </span>
                  <span className="block truncate text-xs text-slate-500">
                    {a.team_name || a.team_id || 'no team'} · {a.submitted_by}
                  </span>
                </span>
                <ValidationStatus status={a.status} />
              </button>
              {openId === a.attempt_id && <AttemptDetail eventRef={eventRef} attemptId={a.attempt_id} />}
            </div>
          ))}
        </div>
      )}
    </QuestCard>
  )
}

function AttemptDetail({ eventRef, attemptId }: { eventRef: string; attemptId: string }) {
  const { data, loading, error } = useApi<HostAttemptDetail>(
    `/api/host/events/${eventRef}/attempts/${attemptId}`,
  )
  if (loading && !data) return <Skeleton className="my-2 h-16 w-full" />
  if (error && !data) return <p className="py-2 text-xs text-[#FB7185]">{error}</p>
  if (!data) return null
  return (
    <div className="mb-3 space-y-2 rounded-xl border border-white/10 bg-white/[0.02] p-3">
      {data.results.length === 0 ? (
        <p className="text-xs text-slate-400">No validator results recorded.</p>
      ) : (
        data.results.map((r) => (
          <div key={r.validation_result_id} className="text-xs">
            <div className="flex items-center gap-2">
              <ValidationStatus status={r.status} />
              <span className="text-slate-400">{r.validator_id}</span>
              {!!r.score_delta && <span className="text-[#FF8A3D]">{r.score_delta > 0 ? '+' : ''}{r.score_delta}</span>}
            </div>
            {r.public_message && <p className="mt-1 text-slate-300">{r.public_message}</p>}
            {r.private_message && (
              <p className="mt-1 flex items-start gap-1.5 rounded-lg border border-amber-500/20 bg-amber-500/[0.05] px-2 py-1 text-amber-200/90">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                <span className="whitespace-pre-wrap font-mono">{r.private_message}</span>
              </p>
            )}
          </div>
        ))
      )}
    </div>
  )
}

// ── Quest pack import / lint ─────────────────────────────────────────────────

function PackImporter() {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState<'lint' | 'import' | null>(null)
  const [out, setOut] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const run = useCallback(
    async (mode: 'lint' | 'import') => {
      setBusy(mode)
      setOut(null)
      setErr(null)
      try {
        const data = await postJson(`/api/host/quest-packs/${mode}`, { manifest_yaml: text })
        if (mode === 'lint') {
          const errs = data?.errors?.length ?? 0
          const warns = data?.warnings?.length ?? 0
          setOut(`Lint: ${errs} error(s), ${warns} warning(s).` + (data?.valid ? ' Valid ✓' : ''))
        } else {
          setOut(`Imported: ${data?.status ?? 'ok'} · version ${data?.pack_version_id ?? '—'}`)
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : `${mode} failed`)
      } finally {
        setBusy(null)
      }
    },
    [text],
  )

  return (
    <QuestCard title="Quest pack import" eyebrow="Content">
      <p className="-mt-1 mb-3 text-sm text-slate-400">
        Paste a quest pack manifest (YAML). Lint first, then import — versions are immutable.
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        spellCheck={false}
        placeholder={'pack:\n  slug: ai-bi-challenge\n  version: 1.0.0\n  title: AI/BI Intelligence Challenge'}
        className="w-full resize-y rounded-xl border border-white/10 bg-[#0B0F18] p-3 font-mono text-xs text-slate-200 outline-none focus:border-[#FF5F1F]/40"
      />
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          onClick={() => run('lint')}
          disabled={!!busy || !text.trim()}
          className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2 text-sm font-medium text-slate-200 transition hover:bg-white/[0.08] disabled:opacity-50"
        >
          <CheckCircle2 className="h-4 w-4" /> {busy === 'lint' ? 'Linting…' : 'Lint'}
        </button>
        <button
          onClick={() => run('import')}
          disabled={!!busy || !text.trim()}
          className="inline-flex items-center gap-2 rounded-xl border border-[#FF5F1F]/35 bg-[#FF5F1F]/15 px-4 py-2 text-sm font-medium text-white transition hover:bg-[#FF5F1F]/25 disabled:opacity-50"
        >
          {busy === 'import' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          {busy === 'import' ? 'Importing…' : 'Import'}
        </button>
        {out && <span className="text-sm text-emerald-300">{out}</span>}
      </div>
      {err && <p className="mt-2 text-sm text-[#FB7185]">{err}</p>}
    </QuestCard>
  )
}

// ── Resource bootstrap & reset (PR08) ────────────────────────────────────────

const RESOURCE_STATUS: Record<string, string> = {
  active: 'text-emerald-300',
  pending: 'text-slate-400',
  failed: 'text-[#FB7185]',
  removed: 'text-slate-500',
}

function ResourcesPanel({ eventRef }: { eventRef: string }) {
  const { data, loading, reload } = useApi<ResourceHealth>(`/api/host/events/${eventRef}/resources`)
  const [busy, setBusy] = useState<string | null>(null)
  const [plan, setPlan] = useState<ResourcePlan | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const act = useCallback(
    async (kind: 'plan-bootstrap' | 'plan-reset' | 'bootstrap' | 'reset') => {
      setBusy(kind)
      setErr(null)
      setMsg(null)
      try {
        if (kind === 'plan-bootstrap' || kind === 'plan-reset') {
          const p = (await postJson(`/api/host/events/${eventRef}/resources/plan`, {
            action: kind === 'plan-reset' ? 'reset' : 'bootstrap',
          })) as ResourcePlan
          setPlan(p)
        } else if (kind === 'bootstrap') {
          const r = await postJson(`/api/host/events/${eventRef}/resources/bootstrap`)
          setMsg(r.ok ? 'Resources provisioned.' : 'Bootstrap completed with errors — see health below.')
          setPlan(null)
          reload()
        } else {
          if (!window.confirm('Drop every team schema for this event? This cannot be undone.')) {
            setBusy(null)
            return
          }
          const r = await postJson(`/api/host/events/${eventRef}/resources/reset`, { confirm: true })
          setMsg(r.ok ? 'Team schemas dropped.' : 'Reset completed with errors — see health below.')
          setPlan(null)
          reload()
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : 'Request failed')
      } finally {
        setBusy(null)
      }
    },
    [eventRef, reload],
  )

  return (
    <QuestCard
      eyebrow="Resources"
      title="Team resource bootstrap & reset"
      action={
        <button onClick={reload} className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-xs text-slate-300 transition hover:bg-white/[0.07]">
          <RefreshCw className="h-3.5 w-3.5" /> Refresh
        </button>
      }
    >
      {loading && !data ? (
        <Skeleton className="h-24 w-full" />
      ) : data?.namespace_error ? (
        <p className="text-sm text-[#FB7185]">Namespace error: {data.namespace_error}</p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
            <span className="inline-flex items-center gap-1.5">
              <Database className="h-3.5 w-3.5 text-[#FF8A3D]" />
              Catalog <code className="text-slate-200">{data?.namespace?.catalog}</code>
            </span>
            <span>
              Schema prefix <code className="text-slate-200">{data?.namespace?.schema_prefix}</code>
            </span>
            {!data?.warehouse_configured && (
              <span className="inline-flex items-center gap-1 text-amber-300">
                <AlertTriangle className="h-3.5 w-3.5" /> No SQL warehouse configured (dry-run only)
              </span>
            )}
          </div>

          {/* Per-team targets + live health */}
          <div className="overflow-hidden rounded-xl border border-white/10">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 bg-white/[0.02] text-left text-[11px] uppercase tracking-wider text-slate-500">
                  <th className="px-3 py-2 font-semibold">Team</th>
                  <th className="px-3 py-2 font-semibold">Target schema</th>
                  <th className="px-3 py-2 font-semibold">Status</th>
                </tr>
              </thead>
              <tbody>
                {(data?.targets ?? []).map((t) => {
                  const health = (data?.resources ?? []).find((r) => r.fqn === t.fqn)
                  const status = health?.status ?? 'not provisioned'
                  return (
                    <tr key={t.fqn} className="border-b border-white/5 last:border-0">
                      <td className="px-3 py-2 text-white">{t.team_name || t.team_id}</td>
                      <td className="px-3 py-2 font-mono text-xs text-slate-300">{t.fqn}</td>
                      <td className={`px-3 py-2 ${RESOURCE_STATUS[status] ?? 'text-slate-500'}`}>{status}</td>
                    </tr>
                  )
                })}
                {(data?.targets ?? []).length === 0 && (
                  <tr>
                    <td colSpan={3} className="px-3 py-4 text-center text-sm text-slate-400">
                      No teams yet — create teams to plan resources.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => act('plan-bootstrap')}
              disabled={!!busy}
              className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:bg-white/[0.08] disabled:opacity-50"
            >
              {busy === 'plan-bootstrap' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Boxes className="h-3.5 w-3.5" />} Dry-run plan
            </button>
            <button
              onClick={() => act('plan-reset')}
              disabled={!!busy}
              title="Preview the DROP statements a reset would run — nothing is executed"
              className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:bg-white/[0.08] disabled:opacity-50"
            >
              {busy === 'plan-reset' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />} Dry-run reset
            </button>
            <button
              onClick={() => act('bootstrap')}
              disabled={!!busy || !data?.warehouse_configured}
              title={data?.warehouse_configured ? undefined : 'Set QUEST_SQL_WAREHOUSE_ID to provision'}
              className="inline-flex items-center gap-1.5 rounded-xl border border-emerald-500/35 bg-emerald-500/15 px-3 py-1.5 text-xs font-medium text-emerald-200 transition hover:bg-emerald-500/25 disabled:opacity-50"
            >
              {busy === 'bootstrap' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Database className="h-3.5 w-3.5" />}
              Bootstrap
            </button>
            <button
              onClick={() => act('reset')}
              disabled={!!busy || !data?.warehouse_configured}
              className="inline-flex items-center gap-1.5 rounded-xl border border-[#F43F5E]/35 bg-[#F43F5E]/10 px-3 py-1.5 text-xs font-medium text-[#FB7185] transition hover:bg-[#F43F5E]/20 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" /> Reset
            </button>
          </div>

          {msg && <p className="text-sm text-emerald-300">{msg}</p>}
          {err && <p className="text-sm text-[#FB7185]">{err}</p>}

          {plan && (
            <div className="rounded-xl border border-white/10 bg-[#0B0F18] p-3">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
                {plan.action} plan — {plan.plan.length} statement(s)
                {plan.blockers.length > 0 && <span className="ml-2 text-[#FB7185]">{plan.blockers.length} blocker(s)</span>}
              </p>
              <ul className="space-y-1">
                {plan.plan.map((i, idx) => (
                  <li key={idx} className={`font-mono text-[11px] ${i.within_namespace ? 'text-slate-300' : 'text-[#FB7185]'}`}>
                    {i.within_namespace ? '· ' : '⚠ '}
                    {i.sql}
                    {i.error && <span className="text-[#FB7185]"> — {i.error}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </QuestCard>
  )
}
