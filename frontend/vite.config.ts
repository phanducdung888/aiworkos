import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    // The API is same-origin in development so the browser never needs CORS and the app never
    // needs to know a second base URL. In the pilot the reverse proxy does the same job.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // e2e is Playwright's; running it under Vitest would start a browser per assertion.
    exclude: ['e2e/**', 'node_modules/**'],
  },
})
