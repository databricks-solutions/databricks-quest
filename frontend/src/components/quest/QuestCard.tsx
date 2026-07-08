import type { PropsWithChildren, ReactNode } from "react"

type QuestCardProps = PropsWithChildren<{
  title?: ReactNode
  eyebrow?: ReactNode
  action?: ReactNode
  className?: string
}>

export function QuestCard({ title, eyebrow, action, className = "", children }: QuestCardProps) {
  return (
    <section className={`quest-card p-3.5 ${className}`}>
      {(title || eyebrow || action) && (
        <div className="relative z-10 mb-2.5 flex items-start justify-between gap-4">
          <div>
            {eyebrow && <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#FF8A3D]">{eyebrow}</div>}
            {title && <h3 className="text-[15px] font-semibold text-white">{title}</h3>}
          </div>
          {action}
        </div>
      )}
      <div className="relative z-10">{children}</div>
    </section>
  )
}
