import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  // icon.png 的唯一真相源——主进程、electron-builder 和渲染器共用同一文件
  publicDir: 'assets',
  css: {
    // 显式钉死空 PostCSS 配置：Tailwind 由 @tailwindcss/vite 处理，无需 PostCSS 插件。不钉的话 Vite 会向上查找 postcss.config.*，用户目录里可能有 Tailwind v3 配置导致 v4 样式表构建失败（"@layer base is used but no matching @tailwind base"）。
    postcss: { plugins: [] }
  },
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      input: {
        sprite: path.resolve(__dirname, 'sprite.html'),
        desktop: path.resolve(__dirname, 'desktop.html'),
        'desktop-companion': path.resolve(__dirname, 'desktop-companion.html'),
        'desktop-background': path.resolve(__dirname, 'desktop-background.html'),
        living: path.resolve(__dirname, 'living.html'),
        workbench: path.resolve(__dirname, 'workbench.html')
      },
      output: {
        manualChunks(id) {
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
      '@client-shared': path.resolve(__dirname, './shared'),
      '@ipc/contracts': path.resolve(__dirname, './shared/ipc/contracts'),
      '@runtime': path.resolve(__dirname, './shared/runtime'),
      '@protocol': path.resolve(__dirname, '../shared/protocol')
    },
    dedupe: ['react', 'react-dom']
  },
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true,
    watch: {
      // 构建目录中的 DLL 可能正在复制或被进程占用。
      ignored: ['**/build/**', '**/dist-electron/**', '**/desktop-host/target/**', '**/release/**']
    }
  },
  preview: {
    host: '127.0.0.1',
    port: 4174
  }
})
