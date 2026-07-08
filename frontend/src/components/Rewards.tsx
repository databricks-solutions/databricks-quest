import { Award, Crown, Gift, Medal, Trophy } from 'lucide-react'
import type { LeaderboardEntry, UserProfile } from '../types'
import { useApi } from '../lib/api'
import { QuestCard } from './quest/QuestCard'
import { EmptyState } from './quest/States'

type LeaderboardResponse = { leaderboard: LeaderboardEntry[] }

const SWAG_TIERS = [
  { place: 1, icon: Crown, color: '#F5B72E', title: 'Champion', items: ['Top weekly recognition'] },
  { place: 2, icon: Medal, color: '#CBD5E1', title: 'Runner-up', items: ['Weekly recognition'] },
  { place: 3, icon: Award, color: '#B45309', title: 'Top 3', items: ['Weekly recognition'] },
]

export default function Rewards({ profile }: { profile?: UserProfile | null }) {
  const { data } = useApi<LeaderboardResponse>('/api/leaderboard?period=weekly')
  const entries = data?.leaderboard ?? []

  const you = entries.find(
    (e) => !!profile && (e.user_id === profile.user_id || e.display_name === profile.display_name),
  )
  const weeklyRank = you?.weekly_rank
  const eligible = typeof weeklyRank === 'number' && weeklyRank <= 3
  const recognitions = profile?.badges ?? []

  return (
    <div className="mx-auto max-w-[1100px] space-y-5">
      <QuestCard className="quest-topography">
        <div className="relative z-10 flex flex-wrap items-center justify-between gap-6">
          <div className="max-w-xl">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#F5B72E]">Weekly eligibility</p>
            <h2 className="mt-2 text-2xl font-bold text-white">
              {eligible ? 'You’re in the top 3 this week' : 'Climb into the top 3 for recognition'}
            </h2>
            <p className="mt-1 text-sm text-slate-300">
              {typeof weeklyRank === 'number'
                ? `You’re currently ranked #${weeklyRank} this week with ${(you?.weekly_points ?? 0).toLocaleString()} points.`
                : 'Earn points this week to enter the weekly rankings.'}
            </p>
          </div>
          <div className={`flex items-center gap-3 rounded-2xl border px-5 py-4 ${eligible ? 'border-[#22C55E]/30 bg-[#22C55E]/[0.08]' : 'border-white/10 bg-white/[0.03]'}`}>
            <Gift className={`h-8 w-8 ${eligible ? 'text-[#22C55E]' : 'text-slate-400'}`} />
            <div>
              <p className="text-sm font-semibold text-white">{eligible ? 'Eligible' : 'Not yet eligible'}</p>
              <p className="text-xs text-slate-400">{eligible ? 'Recognised at week close' : 'Top 3 weekly earn recognition'}</p>
            </div>
          </div>
        </div>
      </QuestCard>

      <div className="grid gap-4 md:grid-cols-3">
        {SWAG_TIERS.map((tier) => {
          const isYours = weeklyRank === tier.place
          return (
            <QuestCard key={tier.place} className={isYours ? 'border-[#FF5F1F]/40' : ''}>
              <div className="relative z-10 text-center">
                <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl" style={{ background: `${tier.color}22`, color: tier.color }}>
                  <tier.icon className="h-7 w-7" />
                </div>
                <div className="mt-3 flex items-center justify-center gap-2">
                  {tier.place === 1 ? <Crown className="h-4 w-4 text-[#F5B72E]" /> : <Medal className="h-4 w-4" style={{ color: tier.color }} />}
                  <p className="text-sm font-bold uppercase tracking-wide" style={{ color: tier.color }}>{tier.place === 1 ? '1st Place' : tier.place === 2 ? '2nd Place' : '3rd Place'}</p>
                </div>
                <p className="mt-1 text-xs text-slate-500">{tier.title}</p>
                <div className="mt-3 space-y-1">
                  {tier.items.map((item) => (
                    <p key={item} className="text-sm text-slate-200">{item}</p>
                  ))}
                </div>
                {isYours && <p className="mt-3 text-xs font-semibold text-[#FF8A3D]">This is your current standing</p>}
              </div>
            </QuestCard>
          )
        })}
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <QuestCard title="How rewards work" eyebrow="Program">
          <ol className="space-y-3 text-sm text-slate-300">
            {[
              'Complete real Databricks platform actions to earn adoption points.',
              'Points roll up into the weekly leaderboard, scored from System Tables.',
              'The top 3 explorers each week earn Databricks recognition.',
              'Program admins recognise top performers shortly after the week closes.',
            ].map((step, i) => (
              <li key={i} className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[#FF5F1F]/15 text-xs font-bold text-[#FF8A3D]">{i + 1}</span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
        </QuestCard>

        <QuestCard title="Recognition history" eyebrow="Your achievements">
          {recognitions.length === 0 ? (
            <EmptyState
              icon={Trophy}
              title="No recognition yet"
              message="Earn badges and place in the weekly top 3 to build your history."
            />
          ) : (
            <div className="space-y-3">
              {recognitions.slice(0, 6).map((b) => (
                <div key={b.badge_id} className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#F5B72E]/15 text-[#F5B72E]">
                    <Trophy className="h-4 w-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-white">{b.badge_name}</p>
                    {b.earned_at && <p className="text-xs text-slate-500">{new Date(b.earned_at).toLocaleDateString()}</p>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </QuestCard>
      </div>
    </div>
  )
}
