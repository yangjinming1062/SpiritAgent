import { defineConfig } from 'tsup'

// preload 走 CJS（沙盒不识别 ESM，理由见 README §4）。
// config 里不放 watch 键：config.watch 一旦 truthy 会把裸 `tsup` 单次构建也劫持成
// watch 模式永不退出；而 CLI 的 `--watch` 又会覆盖 config 值——裸 `--watch` 退化为
// 布尔 true，tsup 改为 watch 整个 "."，chokidar 在 Windows 上对全树（含 node_modules）
// 逐路径 globSync 评估 ignore 列表，持续占满一个核。因此 dev 脚本显式传
// `--watch main --watch shared`（cac 把重复标志收集成数组）。
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
  // esbuild 没有 `resolve.alias` 选项(Vite/Rollup 才有),通过 esbuildOptions
  // 钩子注入别名,使 main/preload 中 `import { IPC } from '@ipc/contracts'` 能
  // 被 esbuild 在打包时解析到 `./shared/ipc/contracts`。
  esbuildOptions(options) {
    options.alias = {
      ...options.alias,
      '@ipc/contracts': './shared/ipc/contracts',
      '@runtime': './shared/runtime'
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
      preload: 'main/preload.ts'
    },
    outDir: 'dist-electron',
    format: ['cjs'],
    outExtension: () => ({ js: '.cjs' }),
    clean: false
  }
])
