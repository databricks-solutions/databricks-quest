import { CheckCircle2, Lock, Star } from "lucide-react"
import { LEVELS, levelInfo } from "../../lib/levels"

type LevelHeroProps = {
  level?: string
  points?: number
  nextLevel?: string
  pointsToNext?: number
  progressPct?: number
  weeklyDelta?: number
  missionsCompleted?: number
  badgesUnlocked?: number
  rankLabel?: string
}

export function LevelHero({
  level = "Bronze Level",
  points = 0,
  nextLevel = "Silver",
  pointsToNext = 0,
  progressPct = 0,
  weeklyDelta = 0,
  missionsCompleted = 0,
  badgesUnlocked = 0,
  rankLabel,
}: LevelHeroProps) {
  const info = levelInfo(points)
  const atMax = !info.next
  const milestones = LEVELS.map((l, i) => ({
    label: l.name,
    state: i < info.currentIndex ? "done" : i === info.currentIndex ? "current" : "locked",
  }))
  const barPct = Math.min(100, Math.max(0, progressPct))

  return (
    <section className="quest-hero quest-topography p-4 lg:p-5">
      <div className="relative z-10 grid gap-5 lg:grid-cols-[1fr_320px]">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#F5B72E]">Current level</div>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-white">{level}</h1>
          <p className="mt-1 text-sm text-slate-200">
            {atMax ? "Top level reached — keep earning to extend your lead" : `${pointsToNext.toLocaleString()} points to ${nextLevel}`}
          </p>
          <div className="mt-3">
            <div className="h-2 rounded-full bg-white/15">
              <div
                className="h-2 rounded-full bg-gradient-to-r from-[#FF5F1F] to-[#F5B72E] shadow-[0_0_24px_rgba(255,95,31,0.45)] transition-all"
                style={{ width: `${barPct}%` }}
              />
            </div>
            <div className="mt-2.5 grid grid-cols-5 gap-2">
              {milestones.map((m) => (
                <div key={m.label} className="flex flex-col items-center gap-1 text-center">
                  <div className="flex h-7 w-7 items-center justify-center rounded-full border border-white/15 bg-black/30">
                    {m.state === "locked" ? <Lock className="h-3.5 w-3.5 text-slate-500" /> : m.state === "current" ? <Star className="h-3.5 w-3.5 text-[#F5B72E]" /> : <CheckCircle2 className="h-3.5 w-3.5 text-slate-300" />}
                  </div>
                  <div className={m.state === "current" ? "text-[11px] font-semibold text-[#F5B72E]" : "text-[11px] text-slate-300"}>{m.label}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="flex flex-col justify-center lg:items-end">
          <div className="text-3xl font-bold tracking-tight text-white">{points.toLocaleString()}<span className="ml-2 text-lg font-medium text-slate-200">pts</span></div>
          <div className="mt-3 grid grid-cols-3 gap-4 text-sm text-slate-300">
            <div><div className="text-base font-semibold text-white">+{weeklyDelta.toLocaleString()}</div><div>this week</div></div>
            <div><div className="text-base font-semibold text-white">{missionsCompleted}</div><div>missions</div></div>
            <div><div className="text-base font-semibold text-white">{badgesUnlocked}</div><div>{badgesUnlocked === 1 ? "badge" : "badges"}</div></div>
          </div>
          {rankLabel && (
            <div className="mt-3 rounded-full border border-white/10 bg-black/30 px-4 py-1.5 text-xs text-slate-200">{rankLabel}</div>
          )}
        </div>
      </div>
    </section>
  )
}
