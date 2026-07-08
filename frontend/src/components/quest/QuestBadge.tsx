type QuestBadgeProps = {
  name: string
  imageSrc: string
  locked?: boolean
  className?: string
}

export function QuestBadge({ name, imageSrc, locked = false, className = "" }: QuestBadgeProps) {
  return (
    <div className={`rounded-xl border border-white/10 bg-white/[0.035] p-2 text-center ${locked ? "opacity-45 grayscale" : "quest-orange-glow"} ${className}`}>
      <img src={imageSrc} alt={name} className="mx-auto h-12 w-12" />
      <div className="mt-1 text-[10px] font-semibold leading-tight text-slate-100">{name}</div>
    </div>
  )
}
