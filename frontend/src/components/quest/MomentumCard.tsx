import type { LucideIcon } from "lucide-react"

type MomentumCardProps = {
  icon: LucideIcon
  label: string
  value: string
  detail: string
  accent?: string
}

export function MomentumCard({ icon: Icon, label, value, detail, accent = "#FF5F1F" }: MomentumCardProps) {
  return (
    <div className="quest-card p-4">
      <div className="relative z-10 flex items-center gap-3.5">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full" style={{ background: `${accent}1F`, color: accent }}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="text-xs text-slate-300">{label}</div>
          <div className="mt-0.5 text-xl font-semibold text-white">{value}</div>
          <div className="mt-0.5 truncate text-xs text-slate-500">{detail}</div>
        </div>
      </div>
    </div>
  )
}
