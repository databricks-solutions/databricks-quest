/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        quest: {
          bg: '#070A12',
          shell: '#0D1320',
          surface: '#111827',
          elevated: '#172033',
          alt: '#1F2937',
          orange: '#FF5F1F',
          orangeLight: '#FF8A3D',
          gold: '#F5B72E',
          cyan: '#00C2D7',
          green: '#22C55E',
          purple: '#8B5CF6',
          blue: '#3B82F6',
          rose: '#F43F5E',
          muted: '#94A3B8',
        },
        level: {
          bronze: '#CD7F32',
          silver: '#CBD5E1',
          gold: '#F5B72E',
          platinum: '#A78BFA',
          elite: '#FF5F1F',
        },
      },
      boxShadow: {
        'quest-orange': '0 0 28px rgba(255, 95, 31, 0.22)',
        'quest-card': '0 18px 60px rgba(0, 0, 0, 0.32)',
      },
      keyframes: {
        'quest-glow': {
          '0%': { boxShadow: '0 0 10px rgba(255,95,31,0.18)' },
          '100%': { boxShadow: '0 0 28px rgba(255,95,31,0.45)' },
        },
        'progress-fill': {
          from: { width: '0%' },
        },
      },
      animation: {
        'quest-glow': 'quest-glow 2.2s ease-in-out infinite alternate',
        'progress-fill': 'progress-fill 1s ease-out',
      },
    },
  },
  plugins: [],
}
