import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Vitest config kept separate from vite build so `npm run build` is unaffected.
// jsdom gives us a DOM for component rendering; globals avoids importing
// describe/it/expect in every spec.
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
})
