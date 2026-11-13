import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AdoptionExport from '../components/AdoptionExport'
import AdminPanel from '../components/AdminPanel'
import { fetchJson } from '../lib/api'

const apiFixture = vi.hoisted(() => ({ statsError: null as string | null }))

vi.mock('../lib/api', () => ({
  fetchJson: vi.fn().mockResolvedValue({ backend: 'lakebase', options: ['lakebase'], warehouse_configured: false }),
  useApi: (url: string) => ({
    data: url.endsWith('/stats')
      ? apiFixture.statsError ? null : { total_users: 0, total_mission_completions: 0, top_missions: [], level_distribution: [], last_refresh: null }
      : { status: 'not_initialized', total_events_scored: 0 },
    loading: false, error: url.endsWith('/stats') ? apiFixture.statsError : null, reload: vi.fn(),
  }),
}))

describe('adoption export', () => {
  beforeEach(() => {
    apiFixture.statsError = null
    vi.mocked(fetchJson).mockResolvedValue({ backend: 'lakebase', options: ['lakebase'], warehouse_configured: false })
    vi.useRealTimers()
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('hides participant exports from non-admins while keeping Insights visible', () => {
    render(<AdminPanel isAdmin={false} />)
    expect(screen.getByText('Total Users')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /export all data/i })).not.toBeInTheDocument()
  })

  it('downloads the ZIP with the server filename and releases the download URL', async () => {
    const blob = new Blob(['zip'], { type: 'application/zip' })
    vi.mocked(fetch).mockResolvedValue(new Response(blob, { headers: {
      'Content-Type': 'application/zip',
      'Content-Disposition': 'attachment; filename="quest-adoption-export-20261113T000000Z.zip"',
    } }))
    const create = vi.fn().mockReturnValue('blob:quest-export')
    const revoke = vi.fn()
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL = create
      static revokeObjectURL = revoke
    })
    let downloaded = ''
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () { downloaded = this.download })
    const timer = vi.spyOn(window, 'setTimeout')
    render(<AdoptionExport />)
    fireEvent.click(screen.getByRole('button', { name: 'Export all data' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Your export download has started.'))
    expect(downloaded).toBe('quest-adoption-export-20261113T000000Z.zip')
    expect(fetch).toHaveBeenCalledWith('/api/admin/export', expect.objectContaining({ cache: 'no-store', credentials: 'same-origin' }))
    expect(create).toHaveBeenCalledTimes(1)
    expect(document.querySelector('a[download]')).toBeNull()
    const release = timer.mock.calls.filter((call) => call[1] === 1000).at(-1)?.[0]
    expect(release).toBeTypeOf('function')
    if (typeof release === 'function') release()
    expect(revoke).toHaveBeenCalledWith('blob:quest-export')
  })

  it('disables repeated clicks while preparing the report', () => {
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}))
    render(<AdoptionExport />)
    fireEvent.click(screen.getByRole('button', { name: 'Export all data' }))
    expect(screen.getByRole('button', { name: /preparing export/i })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /preparing export/i }))
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('status')).toHaveTextContent('Large exports can take a few minutes.')
  })

  it('shows the server error and supports retry', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ error: { message: 'Admin access required.' } }), { status: 403 }))
    render(<AdoptionExport />)
    fireEvent.click(screen.getByRole('button', { name: 'Export all data' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Admin access required.'))
    expect(screen.getByRole('button', { name: 'Export all data' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Export all data' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
  })

  it('does not download a login page returned instead of a ZIP', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('<html>Login</html>', { headers: { 'Content-Type': 'text/html' } }))
    render(<AdoptionExport />)
    fireEvent.click(screen.getByRole('button', { name: 'Export all data' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Refresh the page and try again.'))
    expect(screen.queryByText('Your export download has started.')).not.toBeInTheDocument()
  })

  it('explains an empty scored dataset without disabling catalog downloads', () => {
    render(<AdoptionExport totalUsers={0} />)
    expect(screen.getByText(/No scored users yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export all data' })).toBeEnabled()
  })

  it('handles a network failure and allows another attempt', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Network request failed'))
    render(<AdoptionExport />)
    fireEvent.click(screen.getByRole('button', { name: 'Export all data' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Network request failed'))
    expect(screen.getByRole('button', { name: 'Export all data' })).toBeEnabled()
    expect(screen.queryByText('Your export download has started.')).not.toBeInTheDocument()
  })

  it('aborts a pending request when leaving Insights', () => {
    let signal: AbortSignal | null | undefined
    vi.mocked(fetch).mockImplementation((_url, options) => {
      signal = options?.signal
      return new Promise(() => {})
    })
    const view = render(<AdoptionExport />)
    fireEvent.click(screen.getByRole('button', { name: 'Export all data' }))
    expect(signal?.aborted).toBe(false)
    view.unmount()
    expect(signal?.aborted).toBe(true)
  })

  it('keeps an export running when aggregate telemetry fails', async () => {
    let signal: AbortSignal | null | undefined
    vi.mocked(fetch).mockImplementation((_url, options) => {
      signal = options?.signal
      return new Promise(() => {})
    })
    const view = render(<AdminPanel isAdmin />)
    await waitFor(() => expect(screen.getByRole('button', { name: /lakebase.*active/i })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Export all data' }))
    apiFixture.statsError = 'Telemetry unavailable (504)'
    view.rerender(<AdminPanel isAdmin />)
    expect(screen.getByText('Telemetry unavailable (504)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /preparing export/i })).toBeDisabled()
    expect(signal?.aborted).toBe(false)
  })

  it('shows a useful failure when the server returns a non-JSON error', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('Upstream timed out', { status: 504 }))
    render(<AdoptionExport />)
    fireEvent.click(screen.getByRole('button', { name: 'Export all data' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Export failed (504). Try again.'))
    expect(screen.getByRole('button', { name: 'Export all data' })).toBeEnabled()
  })
})
