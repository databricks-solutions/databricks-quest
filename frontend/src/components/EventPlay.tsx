import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowLeft,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  Clock,
  Flag,
  Lightbulb,
  Loader2,
  Lock,
  Megaphone,
  Send,
  Sparkles,
  Trophy,
  Users,
  XCircle,
} from 'lucide-react'
import type {
  AttemptResult,
  AttemptStatus,
  EventList,
  EventLobby,
  FederationStatus,
  QuestDetail,
  QuestList,
  QuestTask,
  TeamDashboard,
} from '../types'
import { useApi } from '../lib/api'
import { QuestCard } from './quest/QuestCard'
import { EmptyState, ErrorState, Skeleton } from './quest/States'
import { ChildEventView } from './Federation'
import HostConsole from './HostConsole'
import EventLeaderboard from './EventLeaderboard'
import type { Announcement, HintRevealResult } from '../types'

type Tab = 'lobby' | 'quests' | 'team' | 'standings' | 'host'

// ── Status badges ────────────────────────────────────────────────────────────

const STATUS_STYLE: Record<string, string> = {
  active: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  paused: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  frozen: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
  ready: 'border-slate-500/30 bg-slate-500/10 text-slate-300',
  completed: 'border-violet-500/30 bg-violet-500/10 text-violet-300',
  draft: 'border-slate-600/30 bg-slate-600/10 text-slate-400',
  archived: 'border-slate-600/30 bg-slate-600/10 text-slate-500',
}

function EventStatusPill({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[status] ?? STATUS_STYLE.draft}`}>
      {status}
    </span>
  )
}

export function ValidationStatus({ status }: { status: AttemptStatus | string }) {
  const map: Record<string, { cls: string; label: string; Icon: typeof CheckCircle2 }> = {
    passed: { cls: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300', label: 'Passed', Icon: CheckCircle2 },
    failed: { cls: 'border-[#F43F5E]/30 bg-[#F43F5E]/10 text-[#FB7185]', label: 'Failed', Icon: XCircle },
    error: { cls: 'border-amber-500/30 bg-amber-500/10 text-amber-300', label: 'Error', Icon: XCircle },
    manual: { cls: 'border-sky-500/30 bg-sky-500/10 text-sky-300', label: 'Pending review', Icon: Clock },
    queued: { cls: 'border-slate-500/30 bg-slate-500/10 text-slate-300', label: 'Queued', Icon: Loader2 },
    running: { cls: 'border-slate-500/30 bg-slate-500/10 text-slate-300', label: 'Running', Icon: Loader2 },
  }
  const it = map[status] ?? map.running
  const spin = status === 'queued' || status === 'running'
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${it.cls}`}>
      <it.Icon className={`h-3.5 w-3.5 ${spin ? 'animate-spin' : ''}`} /> {it.label}
    </span>
  )
}

// ── Root ─────────────────────────────────────────────────────────────────────

export default function EventPlay({ federation }: { federation: FederationStatus }) {
  // A federated child app is pinned to one event; standalone lets you pick.
  const pinnedEventId = federation.event_id || federation.event_slug || null
  const [selected, setSelected] = useState<string | null>(pinnedEventId)

  if (!selected) return <EventListView onPick={setSelected} />
  return (
    <EventWorkspace
      eventRef={selected}
      federation={federation}
      onBack={pinnedEventId ? undefined : () => setSelected(null)}
    />
  )
}

// ── Event list (lobby chooser) ────────────────────────────────────────────────

function EventListView({ onPick }: { onPick: (id: string) => void }) {
  const { data, loading, loaded, error, reload } = useApi<EventList>('/api/events')
  const events = data?.events ?? []
  const showError = loaded && error && events.length === 0

  return (
    <div className="mx-auto max-w-[1000px] space-y-5">
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <Sparkles className="h-4 w-4 text-[#FF8A3D]" /> Active GameDay events
      </div>
      {showError ? (
        <ErrorState message={error ?? undefined} onRetry={reload} />
      ) : loading && events.length === 0 ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : events.length === 0 ? (
        <EmptyState
          icon={CalendarClock}
          title="No live events right now"
          message="When a host opens an event for joining, it shows up here. Check back when your GameDay starts."
        />
      ) : (
        <div className="grid gap-3">
          {events.map((ev) => (
            <button
              key={ev.event_id}
              onClick={() => onPick(ev.event_id)}
              className="group flex items-center justify-between gap-4 rounded-2xl border border-white/10 bg-white/[0.03] px-5 py-4 text-left transition hover:border-[#FF5F1F]/40 hover:bg-[#FF5F1F]/[0.06]"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2.5">
                  <h3 className="truncate text-base font-semibold text-white">{ev.title}</h3>
                  <EventStatusPill status={ev.status} />
                </div>
                {ev.pack_title && <p className="mt-1 truncate text-sm text-slate-400">{ev.pack_title}</p>}
                <p className="mt-1 text-xs text-slate-500">
                  {typeof ev.team_count === 'number' ? `${ev.team_count} teams` : '—'}
                </p>
              </div>
              <ChevronRight className="h-5 w-5 shrink-0 text-slate-500 transition group-hover:translate-x-0.5 group-hover:text-[#FF8A3D]" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Single event: lobby / quests / team / standings ────────────────────────────

function EventWorkspace({
  eventRef,
  federation,
  onBack,
}: {
  eventRef: string
  federation: FederationStatus
  onBack?: () => void
}) {
  const { data: lobby, loading, loaded, error, reload } = useApi<EventLobby>(`/api/events/${eventRef}`)
  const [tab, setTab] = useState<Tab>('lobby')
  const [activeQuest, setActiveQuest] = useState<string | null>(null)
  const isChild = federation.role === 'child'

  if (loading && !lobby) {
    return (
      <div className="mx-auto max-w-[1000px] space-y-4">
        <Skeleton className="h-10 w-1/3" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }
  if (loaded && error && !lobby) {
    return (
      <div className="mx-auto max-w-[1000px]">
        <ErrorState message={error ?? undefined} onRetry={reload} />
      </div>
    )
  }
  if (!lobby) return null

  const ev = lobby.event
  const tabs: { id: Tab; label: string }[] = [
    { id: 'lobby', label: 'Lobby' },
    { id: 'quests', label: 'Quests' },
    { id: 'team', label: 'Team' },
    { id: 'standings', label: 'Standings' },
    ...(lobby.is_host ? [{ id: 'host' as Tab, label: 'Host' }] : []),
  ]

  return (
    <div className="mx-auto max-w-[1000px] space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {onBack && (
            <button
              onClick={onBack}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-xs text-slate-300 transition hover:bg-white/[0.07]"
            >
              <ArrowLeft className="h-3.5 w-3.5" /> Events
            </button>
          )}
          <h2 className="text-lg font-semibold text-white">{ev.title}</h2>
          <EventStatusPill status={ev.status} />
        </div>
        {!lobby.attempts_open && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/25 bg-amber-500/[0.08] px-3 py-1 text-xs text-amber-200">
            <Lock className="h-3.5 w-3.5" /> Submissions closed
          </span>
        )}
      </div>

      <AnnouncementsBanner eventRef={eventRef} />

      <div className="flex gap-1.5 border-b border-white/10">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => {
              setTab(t.id)
              setActiveQuest(null)
            }}
            className={`-mb-px border-b-2 px-3.5 py-2 text-sm font-medium transition ${
              tab === t.id
                ? 'border-[#FF5F1F] text-white'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'lobby' && <LobbyPanel eventRef={eventRef} lobby={lobby} onJoined={reload} />}
      {tab === 'quests' &&
        (activeQuest ? (
          <QuestRunner
            eventRef={eventRef}
            questId={activeQuest}
            attemptsOpen={lobby.attempts_open}
            joined={lobby.you.joined}
            onBack={() => setActiveQuest(null)}
          />
        ) : (
          <QuestsPanel eventRef={eventRef} onOpen={setActiveQuest} />
        ))}
      {tab === 'team' && <TeamPanel eventRef={eventRef} />}
      {tab === 'standings' &&
        (isChild ? <ChildEventView status={federation} /> : <EventLeaderboard eventRef={eventRef} />)}
      {tab === 'host' && lobby.is_host && <HostConsole eventRef={eventRef} />}
    </div>
  )
}

// ── Player announcements banner ────────────────────────────────────────────

const ANN_STYLE: Record<string, string> = {
  info: 'border-sky-500/25 bg-sky-500/[0.06] text-sky-200',
  warning: 'border-amber-500/25 bg-amber-500/[0.06] text-amber-200',
  critical: 'border-[#F43F5E]/25 bg-[#F43F5E]/[0.06] text-[#FB7185]',
}

function AnnouncementsBanner({ eventRef }: { eventRef: string }) {
  const { data } = useApi<{ announcements: Announcement[] }>(`/api/events/${eventRef}/announcements`)
  const items = (data?.announcements ?? []).slice(0, 3)
  if (items.length === 0) return null
  return (
    <div className="space-y-2">
      {items.map((a) => (
        <div
          key={a.announcement_id}
          className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-sm ${ANN_STYLE[a.severity] ?? ANN_STYLE.info}`}
        >
          <Megaphone className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-semibold">{a.title}</p>
            <p className="mt-0.5 whitespace-pre-wrap opacity-90">{a.body_md}</p>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Lobby: join + team picker ──────────────────────────────────────────────────

function LobbyPanel({
  eventRef,
  lobby,
  onJoined,
}: {
  eventRef: string
  lobby: EventLobby
  onJoined: () => void
}) {
  const [displayName, setDisplayName] = useState('')
  const [teamId, setTeamId] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [newTeam, setNewTeam] = useState('')
  const [renameTo, setRenameTo] = useState('')
  const [selfBusy, setSelfBusy] = useState<'create' | 'rename' | null>(null)
  const [selfErr, setSelfErr] = useState<string | null>(null)

  const selfService = lobby.team_self_service !== false && lobby.joinable

  const createTeam = async () => {
    if (!newTeam.trim()) return
    setSelfBusy('create')
    setSelfErr(null)
    try {
      const res = await fetch(`/api/events/${eventRef}/teams`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newTeam.trim() }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data?.error?.message || `Could not create team (${res.status})`)
      setNewTeam('')
      onJoined()
    } catch (e) {
      setSelfErr(e instanceof Error ? e.message : 'Could not create team')
    } finally {
      setSelfBusy(null)
    }
  }

  const renameTeam = async () => {
    if (!renameTo.trim()) return
    setSelfBusy('rename')
    setSelfErr(null)
    try {
      const res = await fetch(`/api/events/${eventRef}/team/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: renameTo.trim() }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data?.error?.message || `Could not rename team (${res.status})`)
      setRenameTo('')
      onJoined()
    } catch (e) {
      setSelfErr(e instanceof Error ? e.message : 'Could not rename team')
    } finally {
      setSelfBusy(null)
    }
  }

  const join = async () => {
    setBusy(true)
    setErr(null)
    try {
      const res = await fetch(`/api/events/${eventRef}/join`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          display_name: displayName.trim() || undefined,
          team_id: teamId || undefined,
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data?.error?.message || `Join failed (${res.status})`)
      onJoined()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Join failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <QuestCard title="Your status" eyebrow="Lobby">
        {lobby.you.joined ? (
          <div className="space-y-2 text-sm">
            <p className="inline-flex items-center gap-2 text-emerald-300">
              <CheckCircle2 className="h-4 w-4" /> You’re in the event
            </p>
            <p className="text-slate-300">
              Team: <span className="font-semibold text-white">{lobby.you.team_name || 'Not assigned yet'}</span>
            </p>
            {!lobby.you.team_id && (
              <p className="text-xs text-slate-500">
                Pick a team below (or ask your host to assign you) so your points count toward a team.
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-400">
            Join this event to view quests and submit your work. You can join a team now or later.
          </p>
        )}

        {(!lobby.you.joined || !lobby.you.team_id) && lobby.joinable && (
          <div className="mt-4 space-y-3">
            {!lobby.you.joined && (
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Display name (optional)"
                className="w-full rounded-xl border border-white/10 bg-[#0B0F18] px-3 py-2 text-sm text-slate-200 outline-none focus:border-[#FF5F1F]/40"
              />
            )}
            <select
              value={teamId}
              onChange={(e) => setTeamId(e.target.value)}
              className="w-full rounded-xl border border-white/10 bg-[#0B0F18] px-3 py-2 text-sm text-slate-200 outline-none focus:border-[#FF5F1F]/40"
            >
              <option value="">{lobby.teams.length ? 'Choose a team…' : 'No teams yet'}</option>
              {lobby.teams.map((t) => (
                <option key={t.team_id} value={t.team_id}>
                  {t.display_name || t.name} ({t.members})
                </option>
              ))}
            </select>
            <button
              onClick={join}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-xl border border-[#FF5F1F]/35 bg-[#FF5F1F]/15 px-4 py-2 text-sm font-medium text-white transition hover:bg-[#FF5F1F]/25 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Flag className="h-4 w-4" /> {busy ? 'Joining…' : lobby.you.joined ? 'Join team' : 'Join event'}
            </button>
            {err && <p className="text-sm text-[#FB7185]">{err}</p>}
          </div>
        )}
        {!lobby.joinable && !lobby.you.joined && (
          <p className="mt-3 text-sm text-amber-200">This event isn’t open for joining right now.</p>
        )}
      </QuestCard>

      <QuestCard title="Teams" eyebrow={`${lobby.counts.teams} teams · ${lobby.counts.participants} players`}>
        {lobby.teams.length === 0 ? (
          <p className="text-sm text-slate-400">No teams have been created yet.</p>
        ) : (
          <ul className="space-y-2">
            {lobby.teams.map((t) => (
              <li
                key={t.team_id}
                className={`flex items-center justify-between rounded-xl border px-3 py-2 text-sm ${
                  t.team_id === lobby.you.team_id
                    ? 'border-[#FF5F1F]/30 bg-[#FF5F1F]/[0.08]'
                    : 'border-white/10 bg-white/[0.02]'
                }`}
              >
                <span className="flex items-center gap-2 text-slate-200">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ background: t.color || '#FF5F1F' }}
                  />
                  {t.display_name || t.name}
                </span>
                <span className="inline-flex items-center gap-1 text-xs text-slate-400">
                  <Users className="h-3.5 w-3.5" /> {t.members}
                </span>
              </li>
            ))}
          </ul>
        )}
        {selfService && (
          <div className="mt-4 space-y-3 border-t border-white/5 pt-4">
            <div>
              <label className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-slate-500">
                Create a team
              </label>
              <div className="flex gap-2">
                <input
                  value={newTeam}
                  onChange={(e) => setNewTeam(e.target.value)}
                  placeholder="New team name"
                  className="min-w-0 flex-1 rounded-xl border border-white/10 bg-[#0B0F18] px-3 py-2 text-sm text-slate-200 outline-none focus:border-[#FF5F1F]/40"
                />
                <button
                  onClick={createTeam}
                  disabled={selfBusy === 'create' || !newTeam.trim()}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-[#FF5F1F]/35 bg-[#FF5F1F]/15 px-3 py-2 text-sm font-medium text-white transition hover:bg-[#FF5F1F]/25 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Flag className="h-4 w-4" /> {selfBusy === 'create' ? 'Creating…' : 'Create & join'}
                </button>
              </div>
            </div>
            {lobby.you.team_id && (
              <div>
                <label className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-slate-500">
                  Rename your team
                </label>
                <div className="flex gap-2">
                  <input
                    value={renameTo}
                    onChange={(e) => setRenameTo(e.target.value)}
                    placeholder={lobby.you.team_name || 'New display name'}
                    className="min-w-0 flex-1 rounded-xl border border-white/10 bg-[#0B0F18] px-3 py-2 text-sm text-slate-200 outline-none focus:border-[#FF5F1F]/40"
                  />
                  <button
                    onClick={renameTeam}
                    disabled={selfBusy === 'rename' || !renameTo.trim()}
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2 text-sm font-medium text-slate-200 transition hover:bg-white/[0.08] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {selfBusy === 'rename' ? 'Saving…' : 'Rename'}
                  </button>
                </div>
              </div>
            )}
            {selfErr && <p className="text-sm text-[#FB7185]">{selfErr}</p>}
          </div>
        )}
        <div className="mt-4 grid grid-cols-2 gap-2 text-center text-xs text-slate-400">
          <Stat label="Quests" value={lobby.counts.quests} />
          <Stat label="Tasks" value={lobby.counts.tasks} />
        </div>
      </QuestCard>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] py-2.5">
      <div className="text-base font-bold text-[#FF8A3D]">{value}</div>
      <div className="text-[11px] uppercase tracking-wider text-slate-500">{label}</div>
    </div>
  )
}

// ── Quests list ────────────────────────────────────────────────────────────────

function QuestsPanel({ eventRef, onOpen }: { eventRef: string; onOpen: (id: string) => void }) {
  const { data, loading, loaded, error, reload } = useApi<QuestList>(`/api/events/${eventRef}/quests`)
  const quests = data?.quests ?? []
  const showError = loaded && error && quests.length === 0

  if (showError) return <ErrorState message={error ?? undefined} onRetry={reload} />
  if (loading && quests.length === 0)
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-20 w-full" />
        ))}
      </div>
    )
  if (quests.length === 0)
    return (
      <EmptyState
        icon={Flag}
        title="No quests yet"
        message="This event’s quest pack has no quests, or it hasn’t been published yet."
      />
    )

  return (
    <div className="grid gap-3">
      {quests.map((q) => {
        const pct = q.task_count ? Math.round((q.completed_tasks / q.task_count) * 100) : 0
        return (
          <button
            key={q.quest_id}
            onClick={() => onOpen(q.quest_id)}
            className="group rounded-2xl border border-white/10 bg-white/[0.03] px-5 py-4 text-left transition hover:border-[#FF5F1F]/40 hover:bg-[#FF5F1F]/[0.06]"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <h3 className="text-base font-semibold text-white">{q.title}</h3>
                {q.complete && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-300">
                    <CheckCircle2 className="h-3 w-3" /> Done
                  </span>
                )}
                {q.difficulty && <span className="text-xs uppercase tracking-wider text-slate-500">{q.difficulty}</span>}
              </div>
              <ChevronRight className="h-5 w-5 text-slate-500 transition group-hover:translate-x-0.5 group-hover:text-[#FF8A3D]" />
            </div>
            <div className="mt-3 flex items-center gap-3">
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
                <div className="h-full rounded-full bg-[#FF5F1F]" style={{ width: `${pct}%` }} />
              </div>
              <span className="text-xs text-slate-400">
                {q.completed_tasks}/{q.task_count} tasks
              </span>
            </div>
          </button>
        )
      })}
    </div>
  )
}

// ── Quest runner ───────────────────────────────────────────────────────────────

function QuestRunner({
  eventRef,
  questId,
  attemptsOpen,
  joined,
  onBack,
}: {
  eventRef: string
  questId: string
  attemptsOpen: boolean
  joined: boolean
  onBack: () => void
}) {
  const { data, loading, loaded, error, reload } = useApi<QuestDetail>(
    `/api/events/${eventRef}/quests/${questId}`,
  )

  if (loading && !data) return <Skeleton className="h-64 w-full" />
  if (loaded && error && !data) return <ErrorState message={error ?? undefined} onRetry={reload} />
  if (!data) return null

  return (
    <div className="space-y-4">
      <button
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-sm text-slate-400 transition hover:text-slate-200"
      >
        <ArrowLeft className="h-4 w-4" /> All quests
      </button>
      <div>
        <h2 className="text-xl font-semibold text-white">{data.quest.title}</h2>
        {data.quest.narrative && (
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">{data.quest.narrative}</p>
        )}
      </div>
      {!joined && (
        <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-3 text-sm text-amber-200">
          Join the event from the Lobby tab to submit attempts.
        </div>
      )}
      <div className="space-y-3">
        {data.tasks.map((t, i) => (
          <TaskCard
            key={t.task_id}
            eventRef={eventRef}
            task={t}
            index={i + 1}
            canSubmit={attemptsOpen && joined}
            onScored={reload}
          />
        ))}
      </div>
    </div>
  )
}

// ── Task card: submit + validation status + hints ───────────────────────────────

function defaultSubmission(mode: string): string {
  if (mode === 'manual' || mode === 'manual_host_review') return '{\n  "evidence": ""\n}'
  return '{\n  "catalog": "",\n  "schema": ""\n}'
}

function TaskCard({
  eventRef,
  task,
  index,
  canSubmit,
  onScored,
}: {
  eventRef: string
  task: QuestTask
  index: number
  canSubmit: boolean
  onScored: () => void
}) {
  const [open, setOpen] = useState(!task.complete)
  const [submission, setSubmission] = useState(() => defaultSubmission(task.validation_mode))
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<AttemptResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [showHints, setShowHints] = useState(false)
  const [hintBodies, setHintBodies] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      task.hints.filter((h) => h.revealed && h.body_md).map((h) => [h.hint_id, h.body_md as string]),
    ),
  )
  const [revealingHint, setRevealingHint] = useState<string | null>(null)
  const [hintErrors, setHintErrors] = useState<Record<string, string>>({})

  const revealHint = useCallback(
    async (hintId: string) => {
      if (hintBodies[hintId]) return
      setRevealingHint(hintId)
      setHintErrors((prev) => {
        if (!prev[hintId]) return prev
        const next = { ...prev }
        delete next[hintId]
        return next
      })
      try {
        const res = await fetch(`/api/events/${eventRef}/tasks/${task.task_id}/hints/${hintId}/reveal`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        })
        const data = (await res.json().catch(() => ({}))) as HintRevealResult & {
          error?: { message?: string }
        }
        if (!res.ok) throw new Error(data?.error?.message || `Could not reveal hint (${res.status})`)
        setHintBodies((prev) => ({ ...prev, [hintId]: data.hint.body_md || '' }))
        // A newly-applied penalty changed the team score — refresh standings/score.
        if (data.newly_applied) onScored()
      } catch (e) {
        // Surface the failure inline so the player knows to retry rather than
        // silently leaving the hint unrevealed.
        setHintErrors((prev) => ({
          ...prev,
          [hintId]: e instanceof Error ? e.message : 'Could not reveal hint — try again.',
        }))
      } finally {
        setRevealingHint(null)
      }
    },
    [eventRef, task.task_id, hintBodies, onScored],
  )

  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (pollTimer.current) clearTimeout(pollTimer.current)
    },
    [],
  )

  const pollAttempt = useCallback(
    (attemptId: string) => {
      const TERMINAL = new Set(['passed', 'failed', 'manual', 'error'])
      let tries = 0
      const tick = async () => {
        tries += 1
        try {
          const res = await fetch(`/api/events/${eventRef}/attempts/${attemptId}`)
          const data = await res.json().catch(() => ({}))
          const status: string | undefined = data?.attempt?.status
          if (res.ok && status) {
            setResult((prev) =>
              prev && prev.attempt_id === attemptId
                ? {
                    ...prev,
                    status: status as AttemptResult['status'],
                    results: Array.isArray(data.results)
                      ? data.results.map((r: { status: string; public_message?: string | null }) => ({
                          status: r.status,
                          message: r.public_message || '',
                        }))
                      : prev.results,
                  }
                : prev,
            )
            if (TERMINAL.has(status)) {
              if (status === 'passed') onScored()
              return
            }
          }
        } catch {
          // Transient; keep polling until the cap.
        }
        if (tries < 20) {
          pollTimer.current = setTimeout(tick, 2000)
        }
      }
      pollTimer.current = setTimeout(tick, 2000)
    },
    [eventRef, onScored],
  )

  const submit = useCallback(async () => {
    setBusy(true)
    setErr(null)
    let parsed: unknown = {}
    if (submission.trim()) {
      try {
        parsed = JSON.parse(submission)
      } catch {
        setErr('Submission must be valid JSON.')
        setBusy(false)
        return
      }
    }
    try {
      const res = await fetch(`/api/events/${eventRef}/tasks/${task.task_id}/attempts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ submission: parsed }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data?.error?.message || `Submit failed (${res.status})`)
      const attempt = data as AttemptResult
      setResult(attempt)
      if (attempt.status === 'passed') onScored()
      // Forward-looking: synchronous validators return a terminal status here,
      // but async/SDK validators may return queued/running. Poll the attempt
      // until it resolves so the player sees the final outcome without a manual
      // refresh.
      else if ((attempt.status === 'queued' || attempt.status === 'running') && attempt.attempt_id) {
        pollAttempt(attempt.attempt_id)
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Submit failed')
    } finally {
      setBusy(false)
    }
  }, [eventRef, task.task_id, submission, onScored, pollAttempt])

  return (
    <QuestCard className={task.complete ? 'border border-emerald-500/20' : ''}>
      <div className="flex items-start justify-between gap-3">
        <button onClick={() => setOpen((o) => !o)} className="flex flex-1 items-start gap-3 text-left">
          <span
            className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
              task.complete ? 'bg-emerald-500/20 text-emerald-300' : 'bg-white/[0.06] text-[#FF8A3D]'
            }`}
          >
            {task.complete ? <CheckCircle2 className="h-4 w-4" /> : index}
          </span>
          <span>
            <span className="block text-[15px] font-semibold text-white">{task.title}</span>
            <span className="mt-0.5 block text-sm text-slate-400">{task.objective}</span>
          </span>
        </button>
        <span className="shrink-0 text-xs font-semibold text-[#FF8A3D]">+{task.points} pts</span>
      </div>

      {open && (
        <div className="mt-4 space-y-3 border-t border-white/5 pt-4">
          {task.instructions_md && (
            <p className="whitespace-pre-wrap text-sm leading-6 text-slate-300">{task.instructions_md}</p>
          )}
          {task.success_criteria_md && (
            <div className="rounded-xl border border-white/10 bg-white/[0.02] px-3 py-2 text-xs text-slate-400">
              <span className="font-semibold text-slate-300">Success criteria: </span>
              {task.success_criteria_md}
            </div>
          )}

          {task.hints.length > 0 && (
            <div>
              <button
                onClick={() => setShowHints((s) => !s)}
                className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-300 transition hover:text-amber-200"
              >
                <Lightbulb className="h-3.5 w-3.5" />
                {showHints ? 'Hide hints' : `Show hints (${task.hints.length})`}
              </button>
              {showHints && (
                <div className="mt-2 space-y-2">
                  {task.hints.map((h) => {
                    const body = hintBodies[h.hint_id]
                    const revealed = !!body
                    const penalty = Math.abs(h.penalty_points || 0)
                    return (
                      <div key={h.hint_id} className="rounded-xl border border-amber-500/20 bg-amber-500/[0.05] px-3 py-2 text-sm text-amber-100/90">
                        {h.title && <p className="text-xs font-semibold text-amber-200">{h.title}</p>}
                        {revealed ? (
                          <p className="mt-0.5 whitespace-pre-wrap">{body}</p>
                        ) : (
                          <div className="mt-1 flex items-center gap-3">
                            <button
                              onClick={() => revealHint(h.hint_id)}
                              disabled={revealingHint === h.hint_id}
                              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/[0.08] px-2.5 py-1 text-xs font-medium text-amber-200 transition hover:bg-amber-500/[0.15] disabled:opacity-50"
                            >
                              <Lightbulb className="h-3.5 w-3.5" />
                              {revealingHint === h.hint_id
                                ? 'Revealing…'
                                : penalty
                                  ? `Reveal hint (−${penalty} pts)`
                                  : 'Reveal hint'}
                            </button>
                          </div>
                        )}
                        {!revealed && !!penalty && (
                          <p className="mt-1 text-[11px] text-amber-300/70">Costs {penalty} pts when revealed</p>
                        )}
                        {hintErrors[h.hint_id] && (
                          <p className="mt-1 text-[11px] text-[#FB7185]">{hintErrors[h.hint_id]}</p>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          <div>
            <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-slate-500">
              Submission (JSON)
            </label>
            <textarea
              value={submission}
              onChange={(e) => setSubmission(e.target.value)}
              rows={4}
              spellCheck={false}
              className="w-full resize-y rounded-xl border border-white/10 bg-[#0B0F18] p-3 font-mono text-xs text-slate-200 outline-none focus:border-[#FF5F1F]/40"
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={submit}
              disabled={busy || !canSubmit}
              title={canSubmit ? undefined : 'Submissions are closed or you haven’t joined the event.'}
              className="inline-flex items-center gap-2 rounded-xl border border-[#FF5F1F]/35 bg-[#FF5F1F]/15 px-4 py-2 text-sm font-medium text-white transition hover:bg-[#FF5F1F]/25 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send className="h-4 w-4" /> {busy ? 'Validating…' : 'Submit'}
            </button>
            {result && <ValidationStatus status={result.status} />}
            {result && result.points_awarded > 0 && (
              <span className="inline-flex items-center gap-1 text-sm font-semibold text-emerald-300">
                <Trophy className="h-4 w-4" /> +{result.points_awarded} pts
              </span>
            )}
          </div>

          {err && <p className="text-sm text-[#FB7185]">{err}</p>}
          {result && (
            <div className="rounded-xl border border-white/10 bg-white/[0.02] px-3 py-2 text-sm">
              <p className="text-slate-200">{result.message}</p>
              {result.results.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {result.results.map((r, i) => (
                    <li key={i} className="flex items-start gap-2 text-xs text-slate-400">
                      <ValidationStatus status={r.status} />
                      <span className="pt-0.5">{r.message}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </QuestCard>
  )
}

// ── Team dashboard ───────────────────────────────────────────────────────────

function TeamPanel({ eventRef }: { eventRef: string }) {
  const { data, loading, loaded, error, reload } = useApi<TeamDashboard>(`/api/events/${eventRef}/team`)

  if (loading && !data) return <Skeleton className="h-48 w-full" />
  if (loaded && error && !data) return <ErrorState message={error ?? undefined} onRetry={reload} />
  if (!data) return null

  if (!data.team) {
    return (
      <EmptyState
        icon={Users}
        title="You’re not on a team yet"
        message="Join a team from the Lobby tab (or ask your host to assign you) to see your team dashboard."
      />
    )
  }

  const pct = data.progress.total_tasks
    ? Math.round((data.progress.completed_tasks / data.progress.total_tasks) * 100)
    : 0

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <QuestCard title={data.team.display_name} eyebrow="Your team">
        <div className="flex items-center gap-4">
          <div className="rounded-xl border border-[#FF5F1F]/30 bg-[#FF5F1F]/[0.08] px-4 py-3 text-center">
            <div className="text-2xl font-bold text-[#FF8A3D]">{data.score.toLocaleString()}</div>
            <div className="text-[11px] uppercase tracking-wider text-slate-400">points</div>
          </div>
          <div className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3 text-center">
            <div className="text-2xl font-bold text-white">{data.rank ? `#${data.rank}` : '—'}</div>
            <div className="text-[11px] uppercase tracking-wider text-slate-400">rank</div>
          </div>
        </div>
        <div className="mt-4">
          <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
            <span>Progress</span>
            <span>
              {data.progress.completed_tasks}/{data.progress.total_tasks} tasks
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-white/[0.06]">
            <div className="h-full rounded-full bg-[#FF5F1F]" style={{ width: `${pct}%` }} />
          </div>
        </div>
        <div className="mt-4">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Members</p>
          <ul className="space-y-1.5">
            {data.members.map((m) => (
              <li key={m.user_id} className="flex items-center gap-2 text-sm text-slate-300">
                <div className="flex h-6 w-6 items-center justify-center rounded-full bg-white/[0.06] text-[11px] font-bold text-[#FF8A3D]">
                  {(m.display_name || m.user_id || '?')[0].toUpperCase()}
                </div>
                {m.display_name || m.user_id}
              </li>
            ))}
          </ul>
        </div>
      </QuestCard>

      <QuestCard title="Recent scoring" eyebrow="Activity">
        {data.recent.length === 0 ? (
          <p className="text-sm text-slate-400">No points yet. Complete a task to get on the board.</p>
        ) : (
          <ul className="space-y-2">
            {data.recent.map((r) => (
              <li
                key={r.scoring_event_id}
                className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-2 text-sm"
              >
                <span className="truncate text-slate-300">{r.reason}</span>
                <span className={`shrink-0 font-semibold ${r.points_delta >= 0 ? 'text-emerald-300' : 'text-[#FB7185]'}`}>
                  {r.points_delta >= 0 ? '+' : ''}
                  {r.points_delta}
                </span>
              </li>
            ))}
          </ul>
        )}
      </QuestCard>
    </div>
  )
}
