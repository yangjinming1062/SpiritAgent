import { defineConfig } from 'tsup'

// preload 使用沙盒支持的 CJS。监听仅由开发 launcher 传入 main、shared，避免单次构建持续监听或扫描 node_modules。
const baseOptions = {
  bundle: true,
  dts: false,
  external: ['electron', 'electron-log', 'electron-log/main', 'electron-updater', 'ws', 'yaml'],
  ignoreWatch: [
    '**/dist/**',
    '**/dist-electron/**',
    '**/renderer/**',
    '**/node_modules/**',
    '**/.turbo/**',
    '**/.git/**'
  ],
  platform: 'node' as const,
  shims: false,
  sourcemap: true,
  splitting: false,
  target: 'node24',
  esbuildOptions(options) {
    options.alias = {
      ...options.alias,
      '@ipc/contracts': './shared/ipc/contracts',
      '@runtime': './shared/runtime',
      '@protocol': '../shared/protocol'
    }
  }
}

export default defineConfig([
  {
    ...baseOptions,
    entry: {
      entry: 'main/entry.ts'
    },
    outDir: 'dist-electron',
    format: ['esm'],
    outExtension: () => ({ js: '.js' }),
    clean: false
  },
  {
    ...baseOptions,
    entry: {
      preload: 'main/preload.ts',
      'preload-background': 'main/preload-background.ts'
    },
    outDir: 'dist-electron',
    format: ['cjs'],
    outExtension: () => ({ js: '.cjs' }),
    clean: false
  }
])
