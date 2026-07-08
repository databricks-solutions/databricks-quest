import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { BarChart3, CheckCircle2, ChevronRight, Repeat, Shield, X } from 'lucide-react'
import type { Mission } from '../../types'
import { categoryMeta, difficultyForPoints, missionIcon } from '../../lib/mission-meta'

type MissionDrawerProps = {
  mission: Mission | null
  onClose: () => void
}

export function MissionDrawer({ mission, onClose }: MissionDrawerProps) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    if (mission) {
      window.addEventListener('keydown', onKey)
      return () => window.removeEventListener('keydown', onKey)
    }
  }, [mission, onClose])

  if (!mission) return null

  const meta = categoryMeta(mission.category)
  const Icon = missionIcon(mission.icon)
  const done = mission.status === 'completed'
  const difficulty = difficultyForPoints(mission.points)
  const repeatable = mission.award_type === 'repeatable'

  // Render in a portal on <body> so `fixed` anchors to the viewport. Otherwise
  // the animated `.quest-rise` page wrapper (transform persists via animation
  // fill-mode "both") becomes the containing block and the panel scrolls with
  // the page instead of floating.
  return createPortal(
    <div className="fixed inset-0 z-[60]">
      <button
        aria-label="Close mission detail"
        onClick={onClose}
        className="quest-fade-in absolute inset-0 bg-black/60 backdrop-blur-sm"
      />
      <aside className="quest-slide-in absolute right-0 top-0 flex h-full w-full max-w-[440px] flex-col overflow-y-auto border-l border-white/10 bg-[#0D1320] shadow-2xl">
        <div className="relative overflow-hidden border-b border-white/10 p-6">
          <div className="absolute inset-0" style={{ background: `radial-gradient(circle at 85% 0%, ${meta.tint}, transparent 60%)` }} />
          <div className="relative z-10 flex items-start justify-between">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl" style={{ background: meta.tint, color: meta.color }}>
              <Icon className="h-7 w-7" />
            </div>
            <button
              onClick={onClose}
              className="rounded-lg border border-white/10 bg-white/[0.04] p-2 text-slate-300 transition hover:bg-white/[0.08]"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="relative z-10 mt-5">
            <span
              className="inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold"
              style={{ background: meta.tint, color: meta.color }}
            >
              {mission.category}
            </span>
            <h2 className="mt-3 text-2xl font-bold tracking-tight text-white">{mission.name}</h2>
            <p className="mt-2 text-sm leading-6 text-slate-300">{mission.description}</p>
          </div>
        </div>

        <div className="space-y-5 p-6">
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Reward" value={`+${mission.points} pts`} accent="#FF8A3D" />
            <Stat label="Difficulty" value={difficulty} />
            <Stat label="Type" value={repeatable ? 'Repeatable' : 'One-time'} icon={repeatable ? Repeat : undefined} />
            <Stat label="Status" value={done ? 'Completed' : 'Available'} accent={done ? '#22C55E' : undefined} icon={done ? CheckCircle2 : undefined} />
          </div>

          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#FF8A3D]">How it’s detected</p>
            <div className="mt-3 flex items-start gap-3 text-sm text-slate-300">
              <Shield className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />
              <span>
                This mission is awarded automatically from Databricks System Tables. Complete the real platform action and the
                scoring pipeline credits your points on the next refresh — no manual claim required.
              </span>
            </div>
            <div className="mt-4 flex items-start gap-3 text-sm text-slate-300">
              <BarChart3 className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />
              <span>Difficulty is derived from the point value relative to other missions in the catalog.</span>
            </div>
          </div>

          {done && mission.completed_at && (
            <div className="flex items-center gap-2 rounded-xl border border-[#22C55E]/25 bg-[#22C55E]/[0.08] px-4 py-3 text-sm text-[#86EFAC]">
              <CheckCircle2 className="h-4 w-4" />
              Completed on {new Date(mission.completed_at).toLocaleDateString()}
            </div>
          )}

          <a
            href={mission.doc_url || 'https://docs.databricks.com'}
            target="_blank"
            rel="noreferrer"
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[#FF7A1A] to-[#E93D1E] px-5 py-3.5 text-sm font-semibold text-white shadow-xl shadow-[#FF5F1F]/20 transition hover:brightness-110"
          >
            {done ? 'Review documentation' : 'Learn how to complete this'}
            <ChevronRight className="h-4 w-4" />
          </a>
        </div>
      </aside>
    </div>,
    document.body,
  )
}

function Stat({
  label,
  value,
  accent = '#F8FAFC',
  icon: Icon,
}: {
  label: string
  value: string
  accent?: string
  icon?: typeof CheckCircle2
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">{label}</p>
      <p className="mt-1.5 flex items-center gap-1.5 text-base font-semibold" style={{ color: accent }}>
        {Icon && <Icon className="h-4 w-4" />}
        {value}
      </p>
    </div>
  )
}
