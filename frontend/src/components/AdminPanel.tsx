import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  Gauge,
  Plus,
  ShieldCheck,
  Target,
  Trash2,
  TrendingUp,
  Users,
  type LucideIcon,
} from 'lucide-react'
import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { AdminListResponse, AdminStats, PipelineStatus } from '../types'
import { useApi, fetchJson } from '../lib/api'
import { QuestCard } from './quest/QuestCard'
import { EmptyState, ErrorState, Skeleton, SkeletonText } from './quest/States'

const LEVEL_COLORS: Record<string, string> = {
  Bronze: '#CD7F32',
  Silver: '#CBD5E1',
  Gold: '#F5B72E',
  Platinum: '#A78BFA',
  Elite: '#FF5F1F',
}

const BAR_COLORS = ['#FF5F1F', '#00C2D7', '#8B5CF6', '#22C55E', '#F43F5E', '#3B82F6', '#F5B72E', '#EC4899']

const TOOLTIP_STYLE = { background: '#0D1320', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 10, color: '#F1F5F9' }

export default function AdminPanel() {
  const stats = useApi<AdminStats>('/api/admin/stats')
  const pipeline = useApi<PipelineStatus>('/api/admin/pipeline-status')

  const s = stats.data
  const p = pipeline.data
  const isHealthy = p?.status === 'healthy'
  const lastRefresh = s?.last_refresh ? new Date(s.last_refresh).toLocaleString() : 'Never'

  const missionChartData = useMemo(
    () => (s?.top_missions ?? []).slice(0, 8).map((m) => ({ name: m.mission_name || m.mission_id, completions: m.completions })),
    [s],
  )
  const levelChartData = useMemo(
    () => (s?.level_distribution ?? []).map((l) => ({ name: l.level, value: l.cnt, color: LEVEL_COLORS[l.level] || '#64748B' })),
    [s],
  )

  const avgCompletions = s && s.total_users > 0 ? (s.total_mission_completions / s.total_users).toFixed(1) : '0'
  const adoptionBreadth = s ? s.level_distribution.reduce((acc, l) => acc + (l.level !== 'Bronze' ? l.cnt : 0), 0) : 0

  if (stats.error && !s) {
    return (
      <div className="mx-auto max-w-[1280px]">
        <ErrorState message={stats.error} onRetry={() => { stats.reload(); pipeline.reload() }} />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[1280px] space-y-5">
      <QuestCard className={isHealthy ? 'border-[#22C55E]/30' : 'border-[#F5B72E]/30'}>
        <div className="relative z-10 flex flex-wrap items-center gap-4">
          {pipeline.loading && !p ? (
            <Skeleton className="h-6 w-6 rounded-full" />
          ) : isHealthy ? (
            <CheckCircle2 className="h-6 w-6 shrink-0 text-[#22C55E]" />
          ) : (
            <AlertTriangle className="h-6 w-6 shrink-0 text-[#F5B72E]" />
          )}
          <div className="flex-1">
            <h3 className="font-semibold text-white">
              Scoring Pipeline: {isHealthy ? 'Healthy' : p?.status === 'not_initialized' ? 'Not Initialized' : 'Unknown'}
            </h3>
            <p className="mt-0.5 text-sm text-slate-400">
              Last refresh: {lastRefresh}
              {p && p.total_events_scored > 0 && <span className="ml-3">Events scored: {p.total_events_scored.toLocaleString()}</span>}
            </p>
          </div>
          <Clock className="h-5 w-5 text-slate-500" />
        </div>
      </QuestCard>

      <DataBackendToggle />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon={Users} label="Total Users" value={s ? s.total_users.toLocaleString() : undefined} accent="#00C2D7" loading={stats.loading && !s} />
        <Kpi icon={Target} label="Mission Completions" value={s ? s.total_mission_completions.toLocaleString() : undefined} accent="#FF5F1F" loading={stats.loading && !s} />
        <Kpi icon={Activity} label="Avg / User" value={s ? avgCompletions : undefined} accent="#8B5CF6" loading={stats.loading && !s} />
        <Kpi icon={Gauge} label="Past Bronze" value={s ? adoptionBreadth.toLocaleString() : undefined} detail="users leveled up" accent="#22C55E" loading={stats.loading && !s} />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <QuestCard title="Top Mission Completions" eyebrow="Adoption">
          {stats.loading && !s ? (
            <SkeletonText lines={6} />
          ) : missionChartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={missionChartData} layout="vertical" margin={{ left: 0, right: 16 }}>
                <XAxis type="number" tick={{ fill: '#94A3B8', fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="name" tick={{ fill: '#94A3B8', fontSize: 11 }} width={130} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                <Bar dataKey="completions" radius={[0, 6, 6, 0]}>
                  {missionChartData.map((_, i) => (
                    <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState icon={Target} title="No completions yet" message="Mission completions will appear once users start earning points." />
          )}
        </QuestCard>

        <QuestCard title="Level Distribution" eyebrow="Cohorts">
          {stats.loading && !s ? (
            <SkeletonText lines={6} />
          ) : levelChartData.length > 0 ? (
            <div className="flex items-center gap-6">
              <ResponsiveContainer width="55%" height={240}>
                <PieChart>
                  <Pie data={levelChartData} cx="50%" cy="50%" innerRadius={56} outerRadius={92} dataKey="value" stroke="none" paddingAngle={2}>
                    {levelChartData.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-2.5">
                {levelChartData.map((l) => (
                  <div key={l.name} className="flex items-center gap-2 text-sm">
                    <span className="h-3 w-3 rounded-full" style={{ background: l.color }} />
                    <span className="text-slate-300">{l.name}</span>
                    <span className="ml-auto font-semibold text-white">{l.value}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <EmptyState icon={TrendingUp} title="No level data yet" message="Level cohorts populate after the first scoring run." />
          )}
        </QuestCard>
      </div>

      <QuestCard title="Data Sources" eyebrow="Telemetry">
        <div className="grid gap-3 sm:grid-cols-3">
          {[
            { label: 'System Tables', detail: 'Compute, jobs & query telemetry' },
            { label: 'Delta Scoring', detail: 'Point fact & snapshot tables' },
            { label: 'Lakebase Read Model', detail: 'Low-latency profile & leaderboard' },
          ].map((src) => (
            <div key={src.label} className="flex items-start gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-4">
              <Database className="mt-0.5 h-5 w-5 shrink-0 text-[#00C2D7]" />
              <div>
                <p className="text-sm font-semibold text-white">{src.label}</p>
                <p className="mt-0.5 text-xs text-slate-400">{src.detail}</p>
              </div>
            </div>
          ))}
        </div>
      </QuestCard>

      <AdminsManager />
    </div>
  )
}

function AdminsManager() {
  const admins = useApi<AdminListResponse>('/api/admin/admins')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const list = admins.data?.admins ?? []

  async function add(e: React.FormEvent) {
    e.preventDefault()
    const value = email.trim().toLowerCase()
    if (!value) return
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/admin/admins', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: value }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.detail?.error?.message || `Request failed: ${res.status}`)
      }
      setEmail('')
      admins.reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add admin')
    } finally {
      setBusy(false)
    }
  }

  async function remove(target: string) {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`/api/admin/admins/${encodeURIComponent(target)}`, { method: 'DELETE' })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.detail?.error?.message || `Request failed: ${res.status}`)
      }
      admins.reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove admin')
    } finally {
      setBusy(false)
    }
  }

  return (
    <QuestCard title="Admin Access" eyebrow="Governance">
      <div className="relative z-10 space-y-4">
        <p className="text-sm text-slate-400">
          Admins can view this console and manage who else is an admin. The list is stored in Lakebase
          and shared across the master and all child workspaces.
        </p>

        <form onSubmit={add} className="flex flex-wrap items-center gap-2">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@company.com"
            className="min-w-[220px] flex-1 rounded-xl border border-white/10 bg-white/[0.03] px-3.5 py-2.5 text-sm text-white placeholder:text-slate-500 focus:border-[#FF5F1F]/60 focus:outline-none"
          />
          <button
            type="submit"
            disabled={busy || !email.trim()}
            className="inline-flex items-center gap-1.5 rounded-xl bg-[#FF5F1F] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[#ff7440] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Plus className="h-4 w-4" /> Add admin
          </button>
        </form>

        {error && (
          <p className="rounded-lg border border-[#F43F5E]/30 bg-[#F43F5E]/10 px-3 py-2 text-sm text-[#FCA5A5]">{error}</p>
        )}

        {admins.loading && !admins.loaded ? (
          <SkeletonText lines={3} />
        ) : list.length > 0 ? (
          <ul className="divide-y divide-white/5 overflow-hidden rounded-xl border border-white/10">
            {list.map((a) => (
              <li key={a.email} className="flex items-center gap-3 bg-white/[0.02] px-4 py-3">
                <ShieldCheck className="h-4 w-4 shrink-0 text-[#22C55E]" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-white">{a.email}</p>
                  <p className="text-xs text-slate-500">
                    {a.source === 'env'
                      ? 'from deploy config (--admins)'
                      : a.source === 'seed'
                        ? 'seeded at deploy'
                        : a.added_by
                          ? `added by ${a.added_by}`
                          : 'added in-app'}
                  </p>
                </div>
                {a.source === 'env' ? (
                  <span className="rounded-md bg-white/5 px-2 py-1 text-xs text-slate-400">deploy</span>
                ) : (
                  <button
                    onClick={() => remove(a.email)}
                    disabled={busy}
                    title="Remove admin"
                    className="rounded-lg p-2 text-slate-400 transition hover:bg-[#F43F5E]/10 hover:text-[#F43F5E] disabled:opacity-50"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState icon={ShieldCheck} title="No admins recorded" message="Add an email above to grant admin access." />
        )}
      </div>
    </QuestCard>
  )
}

function Kpi({
  icon: Icon,
  label,
  value,
  detail,
  accent,
  loading,
}: {
  icon: LucideIcon
  label: string
  value?: string
  detail?: string
  accent: string
  loading?: boolean
}) {
  return (
    <div className="quest-card p-5">
      <div className="relative z-10">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl" style={{ background: `${accent}22`, color: accent }}>
            <Icon className="h-5 w-5" />
          </div>
          <span className="text-sm text-slate-400">{label}</span>
        </div>
        {loading ? (
          <Skeleton className="mt-3 h-8 w-20" />
        ) : (
          <p className="mt-3 text-3xl font-bold text-white">{value ?? '—'}</p>
        )}
        {detail && <p className="mt-1 text-xs text-slate-500">{detail}</p>}
      </div>
    </div>
  )
}

type BackendInfo = { backend: string; default: string; options: string[]; warehouse_configured: boolean }

function DataBackendToggle() {
  const [info, setInfo] = useState<BackendInfo | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const load = () => fetchJson<BackendInfo>('/api/admin/data-backend').then(setInfo).catch((e) => setErr(String(e)))
  useEffect(() => { load() }, [])
  const switchTo = async (backend: string) => {
    if (busy || backend === info?.backend) return
    setBusy(true); setErr(null)
    try {
      const res = await fetch('/api/admin/data-backend', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ backend }),
      })
      if (!res.ok) { const d = await res.json().catch(() => ({} as any)); throw new Error(d?.error?.message || `HTTP ${res.status}`) }
      await load()
    } catch (e: any) { setErr(e?.message || String(e)) } finally { setBusy(false) }
  }
  const active = info?.backend
  return (
    <QuestCard title="Data Backend" eyebrow="Settings">
      <p className="text-sm text-slate-400">
        Where the app reads adoption data. <strong className="text-slate-200">Warehouse</strong> serves from Delta via the SQL
        warehouse (bypasses Lakebase); <strong className="text-slate-200">Lakebase</strong> serves from Postgres. Changes apply within ~30s.
      </p>
      <div className="mt-4 flex gap-2">
        {(info?.options ?? ['lakebase', 'warehouse']).map((b) => (
          <button
            key={b}
            disabled={busy || (b === 'warehouse' && info != null && !info.warehouse_configured)}
            onClick={() => switchTo(b)}
            className={`rounded-xl border px-4 py-2 text-sm font-semibold capitalize transition disabled:opacity-50 ${
              active === b ? 'border-[#22C55E]/50 bg-[#22C55E]/12 text-white' : 'border-white/10 bg-white/[0.03] text-slate-400 hover:border-white/20 hover:text-slate-200'
            }`}
          >
            {b}{active === b ? ' • active' : ''}
          </button>
        ))}
      </div>
      {info && !info.warehouse_configured && <p className="mt-2 text-xs text-[#F5B72E]">No SQL warehouse is configured for this deployment.</p>}
      {err && <p className="mt-2 text-xs text-[#F43F5E]">{err}</p>}
    </QuestCard>
  )
}
