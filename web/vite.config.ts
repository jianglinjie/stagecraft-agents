/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The console talks to the Python API through this proxy, so the API needs no CORS setup.
// STAGECRAFT_API points it somewhere other than the default `python -m stagecraft.api` port.
const api = process.env.STAGECRAFT_API ?? 'http://127.0.0.1:8000'
const proxy = {
  '/api': { target: api, changeOrigin: true, rewrite: (path: string) => path.replace(/^\/api/, '') },
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: { proxy },
  preview: { proxy },
  // One console bundle; splitting it would only add requests on a local dev tool.
  build: { chunkSizeWarningLimit: 800 },
  test: {
    include: ['src/**/*.test.ts'],
  },
})
