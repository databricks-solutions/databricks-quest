import { useEffect, useRef, useState } from 'react'
import { Download, LoaderCircle } from 'lucide-react'
import { QuestCard } from './quest/QuestCard'

export default function AdoptionExport({ lastRefresh, totalUsers }: { lastRefresh?: string | null; totalUsers?: number }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloaded, setDownloaded] = useState(false)
  const controller = useRef<AbortController | null>(null)

  useEffect(() => () => controller.current?.abort(), [])

  async function download() {
    if (controller.current) return
    const request = new AbortController()
    controller.current = request
    setBusy(true)
    setError(null)
    setDownloaded(false)
    try {
      const response = await fetch('/api/admin/export', {
        signal: request.signal, credentials: 'same-origin', cache: 'no-store',
      })
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(body?.error?.message || body?.detail?.error?.message || `Export failed (${response.status}). Try again.`)
      }
      if (!response.headers.get('Content-Type')?.includes('application/zip')) {
        throw new Error('Quest did not return an export file. Refresh the page and try again.')
      }
      const file = await response.blob()
      const filename = response.headers.get('Content-Disposition')?.match(/filename="(quest-adoption-export-[a-zA-Z0-9-]+\.zip)"/)?.[1]
        || 'quest-adoption-export.zip'
      const url = URL.createObjectURL(file)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      try {
        link.click()
      } finally {
        link.remove()
        // Give the browser time to begin reading the download before releasing it.
        window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      }
      setDownloaded(true)
    } catch (err) {
      if (!request.signal.aborted) {
        setError(err instanceof Error ? err.message : 'Could not export Quest data. Try again.')
      }
    } finally {
      controller.current = null
      setBusy(false)
    }
  }

  return (
    <QuestCard title="Full data export" eyebrow="Reporting">
      <div className="flex flex-wrap items-start justify-between gap-5">
        <div className="min-w-0 flex-1 space-y-2">
          <p className="text-sm text-slate-300">
            Download all users’ scored Quest data as CSV files in one ZIP.
          </p>
          <p className="text-sm text-slate-400">
            Includes profiles and points, every mission award, point history, all leaderboard ranks,
            badges, notifications, training records, and mission and badge catalogs.
          </p>
          <p className="text-xs text-slate-500">
            Administrator access required. Includes all retained data in this deployment’s scoring scope.
            {lastRefresh && ` Scored data last refreshed: ${new Date(lastRefresh).toLocaleString()}.`}
          </p>
          {totalUsers === 0 && (
            <p className="text-xs text-slate-400">No scored users yet. The export still includes catalogs and headers for empty datasets.</p>
          )}
        </div>
        <button
          type="button"
          onClick={download}
          disabled={busy}
          aria-busy={busy}
          className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-[#FF5F1F] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[#ff7440] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#FF5F1F] disabled:cursor-wait disabled:opacity-60"
        >
          {busy ? <LoaderCircle className="h-4 w-4 motion-safe:animate-spin" aria-hidden="true" /> : <Download className="h-4 w-4" aria-hidden="true" />}
          {busy ? 'Preparing export…' : 'Export all data'}
        </button>
      </div>
      {error && <p role="alert" className="mt-3 rounded-lg border border-[#F43F5E]/30 bg-[#F43F5E]/10 px-3 py-2 text-sm text-[#FCA5A5]">{error}</p>}
      <p role="status" aria-live="polite" className="mt-2 text-sm text-slate-400">
        {busy ? 'Preparing all datasets. Large exports can take a few minutes.' : downloaded ? 'Your export download has started.' : ''}
      </p>
    </QuestCard>
  )
}
