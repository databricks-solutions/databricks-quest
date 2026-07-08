import { useState } from 'react'
import { Award, Lock, X } from 'lucide-react'
import type { UserProfile } from '../types'
import { QuestCard } from './quest/QuestCard'
import { Skeleton } from './quest/States'
import { BADGE_CATEGORY_ORDER, decorateBadges, type DecoratedBadge } from '../lib/badges'

export default function BadgeVault({ profile }: { profile?: UserProfile | null }) {
  const [selected, setSelected] = useState<DecoratedBadge | null>(null)

  const decorated = decorateBadges(profile)
  const earnedCount = decorated.filter((b) => b.earned).length
  const pct = Math.round((earnedCount / decorated.length) * 100)
  const loading = !profile

  const grouped = BADGE_CATEGORY_ORDER.map((cat) => ({
    category: cat,
    badges: decorated.filter((b) => b.category === cat),
  })).filter((g) => g.badges.length > 0)

  return (
    <div className="mx-auto max-w-[1280px] space-y-5">
      <QuestCard className="quest-constellation">
        <div className="relative z-10 flex flex-wrap items-center justify-between gap-6">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#F5B72E]">Badge vault</p>
            <h2 className="mt-2 text-2xl font-bold text-white">{loading ? '—' : earnedCount} of {decorated.length} badges unlocked</h2>
            <p className="mt-1 text-sm text-slate-300">Earn badges by completing missions and reaching mastery milestones.</p>
          </div>
          <div className="w-full max-w-sm">
            <div className="mb-2 flex justify-between text-xs text-slate-400">
              <span>Collection progress</span>
              <span className="font-semibold text-[#FF8A3D]">{loading ? '0' : pct}%</span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-white/10">
              <div className="h-full rounded-full bg-gradient-to-r from-[#FF5F1F] to-[#FFB21F] shadow-[0_0_18px_rgba(255,95,31,0.55)]" style={{ width: `${loading ? 0 : pct}%` }} />
            </div>
          </div>
        </div>
      </QuestCard>

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
          {Array.from({ length: 12 }).map((_, i) => (
            <Skeleton key={i} className="h-44 w-full" />
          ))}
        </div>
      ) : (
        grouped.map((group) => (
          <div key={group.category} className="space-y-3">
            <h3 className="text-xs font-bold uppercase tracking-[0.14em] text-slate-400">{group.category}</h3>
            <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
              {group.badges.map((badge) => (
                <button
                  key={badge.id}
                  onClick={() => setSelected(badge)}
                  className={`quest-card group p-4 text-center transition-transform duration-200 hover:-translate-y-0.5 ${badge.earned ? '' : 'opacity-70'}`}
                >
                  <div className="relative z-10">
                    <div className="relative mx-auto h-20 w-20">
                      <img src={badge.image} alt={badge.name} className={`h-20 w-20 ${badge.earned ? 'drop-shadow-[0_0_14px_rgba(255,95,31,0.35)]' : 'grayscale'}`} />
                      {!badge.earned && (
                        <div className="absolute inset-0 flex items-center justify-center">
                          <div className="rounded-full bg-black/55 p-1.5"><Lock className="h-4 w-4 text-slate-300" /></div>
                        </div>
                      )}
                    </div>
                    <p className="mt-3 text-xs font-semibold text-white">{badge.name}</p>
                    <p className={`mt-1 text-[11px] ${badge.earned ? 'text-[#22C55E]' : 'text-slate-500'}`}>{badge.earned ? 'Unlocked' : 'Locked'}</p>
                  </div>
                </button>
              ))}
            </div>
          </div>
        ))
      )}

      {selected && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <button aria-label="Close" onClick={() => setSelected(null)} className="quest-fade-in absolute inset-0 bg-black/65 backdrop-blur-sm" />
          <div className="quest-card quest-slide-in relative z-10 w-full max-w-md p-7 text-center">
            <button onClick={() => setSelected(null)} className="absolute right-4 top-4 rounded-lg border border-white/10 bg-white/[0.04] p-2 text-slate-300 hover:bg-white/[0.08]">
              <X className="h-4 w-4" />
            </button>
            <div className="relative z-10">
              <img src={selected.image} alt={selected.name} className={`mx-auto h-28 w-28 ${selected.earned ? 'drop-shadow-[0_0_22px_rgba(255,95,31,0.4)]' : 'grayscale'}`} />
              <h3 className="mt-4 text-xl font-bold text-white">{selected.name}</h3>
              <span className="mt-2 inline-block rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs text-slate-300">{selected.category}</span>
              <p className="mt-4 text-sm leading-6 text-slate-300">{selected.description}</p>
              <div className="mt-5 rounded-xl border border-white/10 bg-white/[0.03] p-4 text-left">
                <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#FF8A3D]">Requirement</p>
                <p className="mt-1.5 flex items-center gap-2 text-sm text-slate-200">
                  <Award className="h-4 w-4 text-slate-400" /> {selected.requirement}
                </p>
              </div>
              {selected.earned ? (
                <p className="mt-4 text-sm font-medium text-[#22C55E]">
                  Unlocked{selected.earnedAt ? ` on ${new Date(selected.earnedAt).toLocaleDateString()}` : ''}
                </p>
              ) : (
                <p className="mt-4 text-sm text-slate-500">Keep completing missions to unlock this badge.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
