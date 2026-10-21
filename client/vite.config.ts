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
    // Tailwind 由 Vite 插件处理；空配置阻止 Vite 加载仓库外的 PostCSS 配置。
    postcss: { plugins: [] }
  },
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      input: {
        sprite: path.resolve(__dirname, 'sprite.html'),
        desktop: path.resolve(__dirname, 'desktop.html'),
        'desktop-background': path.resolve(__dirname, 'desktop-background.html'),
        main: path.resolve(__dirname, 'main.html')
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
      ignored: ['**/build/**', '**/dist-electron/**', '**/desktop-windows/target/**', '**/release/**']
    }
  },
  preview: {
    host: '127.0.0.1',
    port: 4174
  }
})
