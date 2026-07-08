import type { LucideIcon } from 'lucide-react'
import { AlertTriangle, Inbox, RefreshCw } from 'lucide-react'

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`quest-skeleton rounded-xl bg-white/[0.05] ${className}`} />
}

export function SkeletonText({ lines = 3, className = '' }: { lines?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={`h-3 ${i === lines - 1 ? 'w-2/3' : 'w-full'}`} />
      ))}
    </div>
  )
}

export function SkeletonCard({ className = '' }: { className?: string }) {
  return (
    <div className={`quest-card p-5 ${className}`}>
      <div className="relative z-10 flex items-start gap-4">
        <Skeleton className="h-11 w-11 shrink-0 rounded-lg" />
        <div className="flex-1">
          <Skeleton className="h-4 w-1/2" />
          <div className="mt-3">
            <SkeletonText lines={2} />
          </div>
        </div>
      </div>
    </div>
  )
}

export function EmptyState({
  icon: Icon = Inbox,
  title,
  message,
  action,
}: {
  icon?: LucideIcon
  title: string
  message?: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-white/10 bg-white/[0.02] px-6 py-14 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-white/[0.05] text-slate-400">
        <Icon className="h-6 w-6" />
      </div>
      <h3 className="mt-4 text-sm font-semibold text-white">{title}</h3>
      {message && <p className="mt-1 max-w-md text-sm text-slate-400">{message}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-[#F43F5E]/25 bg-[#F43F5E]/[0.06] px-6 py-12 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[#F43F5E]/15 text-[#FB7185]">
        <AlertTriangle className="h-6 w-6" />
      </div>
      <h3 className="mt-4 text-sm font-semibold text-white">Couldn’t load this data</h3>
      <p className="mt-1 max-w-md text-sm text-slate-400">{message || 'The scoring service may be unavailable. Showing preview data where possible.'}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-5 inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2 text-sm font-medium text-slate-200 transition hover:bg-white/[0.08]"
        >
          <RefreshCw className="h-4 w-4" /> Try again
        </button>
      )}
    </div>
  )
}

export function Spinner({ className = '' }: { className?: string }) {
  return <div className={`h-8 w-8 animate-spin rounded-full border-4 border-[#FF5F1F] border-t-transparent ${className}`} />
}
