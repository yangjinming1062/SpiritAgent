import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  base: '/remote/',
  plugins: [react()],
  publicDir: false,
  css: { postcss: { plugins: [] } },
  resolve: {
    alias: {
      '@protocol': fileURLToPath(new URL('../shared/protocol.ts', import.meta.url))
    }
  },
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    host: '127.0.0.1',
    port: 5175,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:10620', ws: true }
    }
  }
})
