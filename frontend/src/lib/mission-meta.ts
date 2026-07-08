import {
  Activity,
  Award,
  BarChart3,
  Bell,
  Brain,
  Briefcase,
  Calendar,
  CalendarCheck,
  Clock,
  Cpu,
  Database,
  FlaskConical,
  GitBranch,
  LayoutDashboard,
  Layers,
  Play,
  PlayCircle,
  Radio,
  Rocket,
  Search,
  Share2,
  Sparkles,
  Target,
  Trophy,
  UploadCloud,
  Zap,
  type LucideIcon,
} from 'lucide-react'

export const MISSION_ICONS: Record<string, LucideIcon> = {
  rocket: Rocket,
  briefcase: Briefcase,
  'git-branch': GitBranch,
  'play-circle': PlayCircle,
  clock: Clock,
  'upload-cloud': UploadCloud,
  layers: Layers,
  sparkles: Sparkles,
  'layout-dashboard': LayoutDashboard,
  search: Search,
  'bar-chart-2': BarChart3,
  bell: Bell,
  'share-2': Share2,
  share: Share2,
  cpu: Cpu,
  'flask-conical': FlaskConical,
  radio: Radio,
  database: Database,
  play: Play,
  brain: Brain,
  activity: Activity,
  zap: Zap,
  trophy: Trophy,
  'calendar-check': CalendarCheck,
  calendar: Calendar,
  award: Award,
}

export function missionIcon(icon: string): LucideIcon {
  return MISSION_ICONS[icon] || Target
}

type CategoryMeta = { color: string; tint: string }

export const CATEGORY_META: Record<string, CategoryMeta> = {
  'Getting Started': { color: '#22C55E', tint: 'rgba(34,197,94,0.16)' },
  'Data Engineering': { color: '#FF5F1F', tint: 'rgba(255,95,31,0.16)' },
  Analytics: { color: '#00C2D7', tint: 'rgba(0,194,215,0.16)' },
  'AI / ML': { color: '#8B5CF6', tint: 'rgba(139,92,246,0.16)' },
  Streaming: { color: '#3B82F6', tint: 'rgba(59,130,246,0.16)' },
  Consumption: { color: '#F5B72E', tint: 'rgba(245,183,46,0.16)' },
  Engagement: { color: '#F43F5E', tint: 'rgba(244,63,94,0.16)' },
  Governance: { color: '#38BDF8', tint: 'rgba(56,189,248,0.16)' },
  Lakebase: { color: '#10B981', tint: 'rgba(16,185,129,0.16)' },
  // Persona tracks (used as Missions-page tabs)
  'Business Users': { color: '#00C2D7', tint: 'rgba(0,194,215,0.16)' },
  Platform: { color: '#F5B72E', tint: 'rgba(245,183,46,0.16)' },
}

export function categoryMeta(category: string): CategoryMeta {
  return CATEGORY_META[category] || { color: '#94A3B8', tint: 'rgba(148,163,184,0.16)' }
}

const DIFFICULTY_BY_POINTS: [number, string][] = [
  [400, 'Expert'],
  [200, 'Advanced'],
  [100, 'Intermediate'],
  [0, 'Starter'],
]

export function difficultyForPoints(points: number): string {
  for (const [threshold, label] of DIFFICULTY_BY_POINTS) {
    if (points >= threshold) return label
  }
  return 'Starter'
}
