import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  // icon.png 的唯一真相源——main.cjs、electron-builder 和渲染器共用同一文件
  publicDir: 'assets',
  css: {
    // 显式钉死空 PostCSS 配置：Tailwind 由 @tailwindcss/vite 处理，无需 PostCSS 插件。
    // 不钉的话 Vite 会向上查找 postcss.config.*，用户目录里可能有 Tailwind v3 配置
    // 导致 v4 样式表构建失败（"@layer base is used but no matching @tailwind base"）。
    postcss: { plugins: [] }
  },
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      input: {
        index: path.resolve(__dirname, 'index.html'),
        sprite: path.resolve(__dirname, 'sprite.html'),
        living: path.resolve(__dirname, 'living.html'),
        workbench: path.resolve(__dirname, 'workbench.html')
      },
      output: {
        manualChunks(id) {
          if (id.includes('three/addons/') || id.includes('three/examples/')) {
            return 'vendor-three-addons'
          }
          if (id.includes('node_modules/three/') || id.endsWith('/three')) {
            return 'vendor-three-core'
          }
          if (
            id.includes('node_modules/react/') ||
            id.includes('node_modules/react-dom/') ||
            id.includes('node_modules/react-router/') ||
            id.includes('node_modules/react-router-dom/')
          ) {
            return 'vendor-react'
          }
          if (
            id.includes('node_modules/nanostores/') ||
            id.includes('node_modules/@nanostores/') ||
            id.includes('node_modules/@tabler/icons-react/')
          ) {
            return 'vendor-utils'
          }
        }
      }
    }
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './renderer'),
      '@shared': path.resolve(__dirname, './renderer/shared'),
      '@companion': path.resolve(__dirname, './renderer/companion'),
      '@2d': path.resolve(__dirname, './renderer/2d'),
      '@3d': path.resolve(__dirname, './renderer/3d'),
      '@chat': path.resolve(__dirname, './renderer/chat'),
      '@living': path.resolve(__dirname, './renderer/living'),
      '@workbench': path.resolve(__dirname, './renderer/workbench'),
      '@onboarding': path.resolve(__dirname, './renderer/onboarding'),
      '@ipc/contracts': path.resolve(__dirname, './shared/ipc/contracts'),
      '@runtime': path.resolve(__dirname, './shared/runtime')
    },
    dedupe: ['react', 'react-dom']
  },
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true
  },
  preview: {
    host: '127.0.0.1',
    port: 4174
  }
})
