import { useState, useEffect, useCallback, useRef } from 'react'
import {
  LayoutDashboard,
  Target,
  Trophy,
  Settings,
  Bell,
  ChevronRight,
  Gift,
  Award,
  Layers3,
  Network,
} from 'lucide-react'
import DashboardV2 from './components/DashboardV2'
import Missions from './components/Missions'
import Leaderboard from './components/Leaderboard'
import AdminPanel from './components/AdminPanel'
import BadgeVault from './components/BadgeVault'
import Rewards from './components/Rewards'
import Federation from './components/Federation'
import HostConsole from './components/HostConsole'
import EventPlay from './components/EventPlay'
import { BrandLockup } from './components/brand/BrandLockup'
import { levelInfo } from './lib/levels'
import type { Page, UserProfile, Notification, FederationStatus, HealthStatus } from './types'

type NavPage = Page | 'badges' | 'rewards'

type NavItem = { id: NavPage; label: string; icon: typeof LayoutDashboard }

const BASE_NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', label: 'Overview', icon: LayoutDashboard },
  { id: 'missions', label: 'Missions', icon: Target },
  { id: 'leaderboard', label: 'Leaderboard', icon: Trophy },
  { id: 'badges', label: 'Badges', icon: Award },
  { id: 'rewards', label: 'Rewards', icon: Gift },
  { id: 'admin', label: 'Admin', icon: Settings },
]

const pageTitles: Record<NavPage, { title: string; subtitle: string }> = {
  dashboard: { title: 'Overview', subtitle: 'Your journey to Databricks mastery' },
  missions: { title: 'Missions', subtitle: 'Complete real platform actions and earn adoption points' },
  leaderboard: { title: 'Leaderboard', subtitle: 'Weekly competition across Databricks platform explorers' },
  badges: { title: 'Badge Vault', subtitle: 'Track achievements and unlock mastery milestones' },
  rewards: { title: 'Rewards', subtitle: 'Recognition and weekly eligibility' },
  admin: { title: 'Admin', subtitle: 'Platform adoption telemetry and scoring health' },
  federation: { title: 'Event', subtitle: 'Join an event, play quests, and track your team' },
}

const masterPageMeta = { title: 'Host Console', subtitle: 'Workspace health, roster, and identity reconciliation' }

const levelColor: Record<string, string> = {
  Bronze: '#CD7F32',
  Silver: '#CBD5E1',
  Gold: '#F5B72E',
  Platinum: '#A78BFA',
  Elite: '#FF5F1F',
  Legend: '#FF5F1F',
}

export default function App() {
  const [page, setPage] = useState<NavPage>('dashboard')
  const mainRef = useRef<HTMLElement>(null)
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [showNotifs, setShowNotifs] = useState(false)
  const [federation, setFederation] = useState<FederationStatus | null>(null)
  const [health, setHealth] = useState<HealthStatus | null>(null)

  // Reset the scroll container to the top when switching tabs, so a new (often
  // shorter) page never opens scrolled past its content and looking blank.
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 })
  }, [page])

  const fetchProfile = useCallback(async () => {
    try {
      const res = await fetch('/api/profile')
      if (res.ok) setProfile(await res.json())
    } catch {
      // Keep UI usable during local frontend work.
    }
  }, [])

  const fetchNotifications = useCallback(async () => {
    try {
      const res = await fetch('/api/notifications')
      if (res.ok) {
        const data = await res.json()
        setNotifications(data.notifications || [])
      }
    } catch {
      // Keep UI usable during local frontend work.
    }
  }, [])

  const fetchFederation = useCallback(async () => {
    try {
      const res = await fetch('/api/federation/status')
      if (res.ok) setFederation(await res.json())
    } catch {
      // Standalone deployments may not expose this; ignore.
    }
  }, [])

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch('/api/health')
      if (res.ok) setHealth(await res.json())
    } catch {
      // Health is best-effort; the badge simply hides if unavailable.
    }
  }, [])

  useEffect(() => {
    fetchProfile()
    fetchNotifications()
    fetchFederation()
    fetchHealth()
  }, [fetchProfile, fetchNotifications, fetchFederation, fetchHealth])

  // Admin page is gated server-side (QUEST_ADMIN_ALLOWLIST); hide the nav item
  // unless the profile says this user is an admin. Admin is the last base item,
  // so dropping it keeps the dashboard/missions indices stable below.
  const baseNav = BASE_NAV_ITEMS.filter((i) => i.id !== 'admin' || profile?.is_admin)
  // Event Mode is opt-in and server-driven: the Event nav only appears when the
  // backend reports it on (federation/status 404s otherwise, leaving this null).
  const isEventMode = !!federation && federation.event_mode !== false
  const navItems: NavItem[] = isEventMode
    ? [
        baseNav[0],
        ...(federation?.role === 'child' ? [baseNav[1]] : []),
        { id: 'federation' as NavPage, label: federation?.role === 'master' ? 'Host Console' : 'Event', icon: Network },
        ...baseNav.slice(2),
      ]
    : baseNav

  const activeMeta =
    page === 'federation' && federation?.role === 'master' ? masterPageMeta : pageTitles[page]
  const totalPoints = profile?.total_points ?? 0
  const level = profile?.level ?? 'Bronze'
  const info = levelInfo(totalPoints)
  const progressPct = profile?.level_progress?.progress_pct ?? info.progressPct
  const nextLevel = info.next?.name ?? 'Max'
  const pointsToNext = profile?.level_progress
    ? Math.max(profile.level_progress.level_ceiling - totalPoints, 0)
    : info.pointsToNext
  const nextLevelPoints = profile?.level_progress?.level_ceiling ?? info.next?.threshold ?? totalPoints

  return (
    <div className="flex h-screen overflow-hidden bg-[#070A12] text-slate-100">
      <aside className="relative flex w-[280px] shrink-0 flex-col overflow-hidden border-r border-white/10 bg-[#0D1320]/95">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_10%,rgba(255,95,31,0.12),transparent_32%)]" />
        <div className="relative z-10 border-b border-white/10 p-6">
          <BrandLockup />
        </div>

        <div className="relative z-10 mx-4 mt-5 rounded-2xl border border-white/10 bg-white/[0.045] p-4 shadow-2xl shadow-black/20">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gradient-to-br from-[#7A4A24] to-[#2B1B16] text-lg font-bold text-white ring-1 ring-white/10">
              {(profile?.display_name || 'A')[0].toUpperCase()}
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-white">{profile?.display_name || 'Explorer'}</p>
              <p className="mt-1 text-xs font-semibold" style={{ color: levelColor[level] || '#F5B72E' }}>◆ {level}</p>
            </div>
          </div>
          <div className="mt-4">
            <div className="mb-2 flex justify-between text-xs text-slate-400">
              <span className="text-slate-200">{totalPoints.toLocaleString()} pts</span>
              <span>{nextLevelPoints.toLocaleString()} pts</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full bg-gradient-to-r from-[#FF5F1F] to-[#FFB21F] shadow-[0_0_18px_rgba(255,95,31,0.55)]"
                style={{ width: `${Math.min(progressPct, 100)}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-slate-400">
              {nextLevel === 'Max' ? 'Top level reached' : `${pointsToNext.toLocaleString()} pts to ${nextLevel}`}
            </p>
          </div>
        </div>

        <nav className="relative z-10 mt-5 flex-1 space-y-1 px-4">
          {navItems.map((item) => {
            const active = page === item.id
            const Icon = item.icon
            return (
              <button
                key={item.id}
                onClick={() => setPage(item.id)}
                className={`group flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-sm font-medium transition-all ${
                  active
                    ? 'border-[#FF5F1F]/35 bg-[#FF5F1F]/14 text-white shadow-lg shadow-[#FF5F1F]/10'
                    : 'border-transparent text-slate-400 hover:border-white/10 hover:bg-white/[0.045] hover:text-white'
                }`}
              >
                <Icon className={`h-5 w-5 ${active ? 'text-[#FF8A3D]' : 'text-slate-500 group-hover:text-slate-300'}`} />
                <span>{item.label}</span>
                {active && <ChevronRight className="ml-auto h-4 w-4 text-[#FF8A3D]" />}
              </button>
            )
          })}
        </nav>

        <div className="relative z-10 border-t border-white/10 p-5">
          <div className="flex items-start gap-3 rounded-xl bg-white/[0.035] p-3 text-xs text-slate-400">
            <Layers3 className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" />
            <div>
              <p className="font-medium text-slate-300">Turn platform adoption into mastery.</p>
              <p className="mt-3 text-slate-500">Powered by System Tables, Delta, Lakebase and Databricks Apps.</p>
            </div>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="relative z-30 flex h-[64px] shrink-0 items-center justify-between border-b border-white/10 bg-[#0D1320]/80 px-7 backdrop-blur-xl">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-white">{activeMeta.title}</h1>
            <p className="mt-1 text-sm text-slate-400">{activeMeta.subtitle}</p>
          </div>
          <div className="flex items-center gap-3">
            {health && <HealthBadge health={health} isAdmin={!!profile?.is_admin} />}
            <button className="rounded-xl border border-white/10 bg-white/[0.035] px-4 py-2 text-sm font-medium text-slate-200 hover:bg-white/[0.06]">
              This week
            </button>
            <div className="relative">
              <button
                onClick={() => setShowNotifs(!showNotifs)}
                className="relative rounded-xl border border-white/10 bg-white/[0.035] p-2.5 hover:bg-white/[0.06]"
              >
                <Bell className="h-5 w-5 text-slate-300" />
                {notifications.length > 0 && <span className="absolute right-2 top-2 h-2 w-2 rounded-full bg-[#FF5F1F]" />}
              </button>
              {showNotifs && (
                <div className="quest-card absolute right-0 z-50 mt-3 max-h-96 w-96 overflow-y-auto p-0 shadow-2xl">
                  <div className="border-b border-white/10 p-4 text-sm font-semibold text-white">Notifications</div>
                  {notifications.length === 0 ? (
                    <div className="p-4 text-sm text-slate-500">No notifications yet</div>
                  ) : (
                    notifications.slice(0, 10).map((n, i) => (
                      <div key={i} className="border-b border-white/5 p-4 hover:bg-white/[0.035]">
                        <p className="text-sm font-semibold text-white">{n.title}</p>
                        <p className="mt-1 text-xs leading-5 text-slate-400">{n.message}</p>
                        {n.points > 0 && <span className="mt-2 inline-block text-xs font-semibold text-[#F5B72E]">+{n.points} pts</span>}
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
            <div className="rounded-xl border border-[#FF5F1F]/25 bg-[#FF5F1F]/8 px-4 py-2 text-sm">
              <span className="font-bold text-[#FF8A3D]">{totalPoints.toLocaleString()}</span>
              <span className="ml-1 text-slate-400">pts</span>
            </div>
          </div>
        </header>

        <main ref={mainRef} className="quest-grid-bg min-w-0 flex-1 overflow-y-auto p-4 lg:p-5">
          <div key={page} className="quest-rise">
            {page === 'dashboard' && <DashboardV2 profile={profile} onRefresh={fetchProfile} notifications={notifications} onNavigate={(p) => setPage(p as NavPage)} />}
            {page === 'missions' && <Missions onProfileRefresh={fetchProfile} />}
            {page === 'leaderboard' && <Leaderboard profile={profile} />}
            {page === 'badges' && <BadgeVault profile={profile} />}
            {page === 'rewards' && <Rewards profile={profile} />}
            {page === 'admin' && profile?.is_admin && <AdminPanel />}
            {page === 'federation' && federation &&
              (federation.role === 'master' ? (
                federation.event_id || federation.event_slug ? (
                  // Master gets the full host console (lifecycle, teams,
                  // attempts, announcements, adjustments, resources, report,
                  // pack import) with the federation-only panels folded in.
                  <HostConsole
                    eventRef={(federation.event_id || federation.event_slug) as string}
                    federation={federation}
                  />
                ) : (
                  // No event resolved yet — Federation renders the guidance
                  // empty state (set QUEST_EVENT_SLUG via deploy --event).
                  <Federation status={federation} />
                )
              ) : (
                <EventPlay federation={federation} />
              ))}
          </div>
        </main>
      </div>
    </div>
  )
}

function HealthBadge({ health, isAdmin }: { health: HealthStatus; isAdmin: boolean }) {
  const ok = health.status === 'ok' && health.db_connected
  const roleLabel =
    health.role === 'master' ? 'Master' : health.role === 'child' ? 'Child' : 'Standalone'
  return (
    <div
      className="hidden items-center gap-2 rounded-xl border border-white/10 bg-white/[0.035] px-3 py-2 text-xs sm:flex"
      title={`Backend ${health.status} · DB ${health.db_connected ? 'connected' : 'unreachable'} · role ${health.role} · event mode ${health.event_mode ? 'on' : 'off'}`}
    >
      <span className={`h-2 w-2 rounded-full ${ok ? 'bg-emerald-400' : 'bg-[#FB7185]'}`} />
      {health.event_mode ? (
        <span className="font-medium text-slate-200">{roleLabel}</span>
      ) : (
        <span className="font-medium text-slate-200">Adoption</span>
      )}
      {isAdmin && <span className="rounded bg-[#FF5F1F]/15 px-1.5 py-0.5 font-semibold text-[#FF8A3D]">Admin</span>}
    </div>
  )
}
