import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { UI_THEME_URL_PARAM } from '@ipc/contracts'

import { directoryExists, fileExists } from '../shared/utils'

function unpackedPathFor(filePath: string): string {
  return filePath.replace(/app\.asar(?=$|[\\/])/, 'app.asar.unpacked')
}

/** 应用图标取首个存在的候选：Windows 任务栏/窗口图标优先 .ico（多尺寸位图）；macOS dock 用 png 即可。 */
export function createAppIconResolver(appRoot: string): () => null | string {
  const candidates = [
    ...(process.platform === 'win32' ? [path.join(appRoot, 'assets', 'icon.ico')] : []),
    path.join(appRoot, 'assets', 'icon.png'),
    path.join(appRoot, 'assets', 'icon.ico'),
    path.join(process.resourcesPath, 'icon.ico'),
    path.join(unpackedPathFor(appRoot), 'icon.ico')
  ]

  return () => candidates.find(fileExists) || null
}

interface RendererPathsDeps {
  appRoot: string
  devServer?: null | string
  isPackaged: boolean
  rememberLog: (chunk: string) => void
}

/** 渲染层 HTML 定位：dev server → appRoot/dist → SPIRITAGENT_DESKTOP_WEB_DIST → asar.unpacked/dist；都缺失时记日志并返回首个候选。 */
export function createRendererPaths({ appRoot, devServer, isPackaged, rememberLog }: RendererPathsDeps) {
  function resolveWebDist(): string {
    const override = process.env.SPIRITAGENT_DESKTOP_WEB_DIST

    if (override && directoryExists(path.resolve(override))) {
      return path.resolve(override)
    }

    const unpackedDist = path.join(unpackedPathFor(appRoot), 'dist')

    if (directoryExists(unpackedDist)) {
      return unpackedDist
    }

    const fallback = path.join(appRoot, 'dist')

    if (isPackaged && /app\.asar(?=$|[\\/])/.test(fallback) && !directoryExists(fallback)) {
      rememberLog(
        `[web-dist] renderer dist resolved to an asar-internal path that ` +
          `is not a real directory: ${fallback}. Renderer pages will fail to load. ` +
          'Ensure dist/** is unpacked (asarUnpack) or set SPIRITAGENT_DESKTOP_WEB_DIST.'
      )
    }

    return fallback
  }

  function htmlFileNameForRole(role?: string): string {
    if (role === 'sprite') {
      return 'sprite.html'
    }

    if (role === 'workbench') {
      return 'workbench.html'
    }

    return 'living.html'
  }

  function resolveRendererHtml(htmlFileName = 'living.html'): string {
    const candidates = [path.join(appRoot, 'dist', htmlFileName), path.join(resolveWebDist(), htmlFileName)]
    const found = candidates.find(fileExists)

    if (found) {
      return found
    }

    rememberLog(
      `[renderer] ${htmlFileName} not found — the desktop app was packaged without a ` +
        'renderer bundle. Tried: ' +
        candidates.join(', ') +
        '. Rebuild via the Tauri SpiritAgent-Setup installer.'
    )

    return candidates[0]
  }

  function rendererUrlFor(role: string, theme?: string): string {
    const htmlFile = htmlFileNameForRole(role)
    let url: URL

    if (devServer) {
      url = new URL(`${devServer}/${htmlFile}`)
    } else {
      url = new URL(pathToFileURL(resolveRendererHtml(htmlFile)).toString())
    }

    if (theme) {
      url.searchParams.set(UI_THEME_URL_PARAM, theme)
    }

    return url.toString()
  }

  return { rendererUrlFor }
}
