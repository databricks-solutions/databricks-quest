// Mirror of the backend LEVEL_THRESHOLDS in app/main.py. Keep in sync.
export const LEVELS: { name: string; threshold: number }[] = [
  { name: 'Bronze', threshold: 0 },
  { name: 'Silver', threshold: 300 },
  { name: 'Gold', threshold: 800 },
  { name: 'Platinum', threshold: 2000 },
  { name: 'Elite', threshold: 5000 },
]

export type LevelInfo = {
  current: { name: string; threshold: number }
  next: { name: string; threshold: number } | null
  currentIndex: number
  pointsToNext: number
  progressPct: number
}

export function levelInfo(points: number): LevelInfo {
  let currentIndex = 0
  for (let i = 0; i < LEVELS.length; i++) {
    if (points >= LEVELS[i].threshold) currentIndex = i
  }
  const current = LEVELS[currentIndex]
  const next = LEVELS[currentIndex + 1] ?? null
  const pointsToNext = next ? Math.max(next.threshold - points, 0) : 0
  const progressPct = next
    ? Math.min(100, Math.max(0, Math.round(((points - current.threshold) / (next.threshold - current.threshold)) * 100)))
    : 100
  return { current, next, currentIndex, pointsToNext, progressPct }
}
