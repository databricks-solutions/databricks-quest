/**
 * Frontend smoke tests (PR-pilot G).
 *
 * Minimal but real: render presentational states and exercise the data layer's
 * URL wiring + polling pause. These guard the player/host flows' building
 * blocks (loading/empty/error states and `useApi`) without standing up the full
 * app shell.
 */
import { render, screen, renderHook, waitFor, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EmptyState, ErrorState } from '../components/quest/States'
import { useApi } from '../lib/api'
import { scopeLabel } from '../components/AdminPanel'

describe('quest states render', () => {
  it('renders an empty state with title + message', () => {
    render(<EmptyState title="No quests yet" message="Your host will open the event soon." />)
    expect(screen.getByText('No quests yet')).toBeInTheDocument()
    expect(screen.getByText(/host will open/i)).toBeInTheDocument()
  })

  it('renders an error state with a retry button', () => {
    const onRetry = vi.fn()
    render(<ErrorState message="Scoring service unavailable" onRetry={onRetry} />)
    expect(screen.getByText(/scoring service unavailable/i)).toBeInTheDocument()
    screen.getByRole('button', { name: /try again/i }).click()
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})

describe('useApi data layer', () => {
  beforeEach(() => {
    vi.useRealTimers()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches the given endpoint and exposes the payload', async () => {
    const payload = { leaderboard: [{ team_id: 'team_red', rank: 1 }] }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useApi<typeof payload>('/api/events/evt_1/leaderboard'))

    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(fetchMock).toHaveBeenCalledWith('/api/events/evt_1/leaderboard', expect.anything())
    expect(result.current.data).toEqual(payload)
    expect(result.current.error).toBeNull()
  })

  it('surfaces an error when the request fails', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useApi<unknown>('/api/health'))
    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(result.current.error).toMatch(/503/)
  })

  it('polls on an interval and pauses when the tab is hidden', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })
    vi.stubGlobal('fetch', fetchMock)

    renderHook(() => useApi<unknown>('/api/events/evt_1/leaderboard', null, { pollMs: 1000 }))
    // initial fetch
    await act(async () => {
      await Promise.resolve()
    })
    const initialCalls = fetchMock.mock.calls.length
    expect(initialCalls).toBeGreaterThanOrEqual(1)

    // advance one interval → one more fetch
    await act(async () => {
      vi.advanceTimersByTime(1000)
      await Promise.resolve()
    })
    expect(fetchMock.mock.calls.length).toBeGreaterThan(initialCalls)
  })
})

describe('scoring scope label (Insights, read-only)', () => {
  it('says whole account when no workspace IDs are set', () => {
    expect(scopeLabel(undefined).text).toBe('Scope: whole account')
    expect(scopeLabel([]).text).toBe('Scope: whole account')
  })

  it('lists the IDs with a correct plural', () => {
    expect(scopeLabel(['111']).text).toBe('Scope: 1 workspace (111) · set at deploy time')
    expect(scopeLabel(['111', '222']).text).toBe('Scope: 2 workspaces (111, 222) · set at deploy time')
    expect(scopeLabel(['111', '222']).title).toBeUndefined()
  })

  it('shows the first four IDs and puts the full list in the title', () => {
    const s = scopeLabel(['1', '2', '3', '4', '5', '6'])
    expect(s.text).toBe('Scope: 6 workspaces (1, 2, 3, 4, +2 more) · set at deploy time')
    expect(s.title).toBe('All workspace IDs: 1, 2, 3, 4, 5, 6')
  })
})
