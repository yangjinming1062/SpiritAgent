import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vite'

export default defineConfig({
  publicDir: false,
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    lib: {
      entry: fileURLToPath(new URL('../shared/api.ts', import.meta.url)),
      name: 'SpiritAgentHttp',
      formats: ['iife'],
      fileName: () => 'admin-http.js'
    }
  }
})
