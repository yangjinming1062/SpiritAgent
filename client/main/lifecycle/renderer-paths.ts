import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { UI_THEME_URL_PARAM } from '@ipc/contracts'

import { directoryExists, fileExists } from '../shared/utils'

export function unpackedPathFor(filePath: string): string {
  return filePath.replace(/app\.asar(?=$|[\\/])/, 'app.asar.unpacked')
}

interface RendererPathsDeps {
  appRoot: string
  devServer?: null | string
  isPackaged: boolean
  rememberLog: (chunk: string) => void
}

/** 渲染层 HTML 定位：dev server → asar.unpacked dist → asar dist，失败仍返回候选路径。 */
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
        `[web-dist] dashboard frontend dir resolved to an asar-internal path that ` +
          `is not a real directory: ${fallback}. Static routes will 404. ` +
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
