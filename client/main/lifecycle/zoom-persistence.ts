import fs from 'node:fs'
import path from 'node:path'

import { clamp } from '@runtime'
import type { App, BrowserWindow } from 'electron'

import { errorMessage, safeReadJson } from '../shared/utils'

// 缩放级别的本地持久化——Electron BrowserWindow 内置 setZoomLevel，但需要把当前值
// 落盘才能在重启后还原。文件位于 userData/desktop-zoom.json。
const ZOOM_FILE = 'desktop-zoom.json'

// Electron 的合法范围是 -9 到 9；超出此区间会让 setZoomLevel 抛错。
function clampZoomLevel(value: number): number {
  if (!Number.isFinite(value)) {
    return 0
  }

  return clamp(value, -9, 9)
}

interface ZoomPersistenceOptions {
  app: Pick<App, 'getPath'>
  rememberLog: (chunk: string) => void
}

export function createZoomPersistence({ app, rememberLog }: ZoomPersistenceOptions) {
  function readPersistedZoomLevel(): number | null {
    const parsed = safeReadJson<{ zoomLevel?: unknown }>(path.join(app.getPath('userData'), ZOOM_FILE))

    if (parsed && typeof parsed.zoomLevel === 'number') {
      return clampZoomLevel(parsed.zoomLevel)
    }

    return null
  }

  function writePersistedZoomLevel(zoomLevel: number): void {
    try {
      const filePath = path.join(app.getPath('userData'), ZOOM_FILE)
      fs.writeFileSync(filePath, JSON.stringify({ zoomLevel }), 'utf8')
    } catch (error: unknown) {
      rememberLog(`[zoom] persist failed: ${errorMessage(error)}`)
    }
  }

  return {
    restorePersistedZoomLevel(targetWin: BrowserWindow | null): void {
      if (!targetWin || targetWin.isDestroyed()) {
        return
      }

      const stored = readPersistedZoomLevel()

      if (stored !== null) {
        targetWin.webContents.setZoomLevel(stored)
      }
    },
    setAndPersistZoomLevel(targetWin: BrowserWindow | null, zoomLevel: number): void {
      if (!targetWin || targetWin.isDestroyed()) {
        return
      }

      const next = clampZoomLevel(zoomLevel)
      targetWin.webContents.setZoomLevel(next)
      writePersistedZoomLevel(next)
    }
  }
}

export type ZoomPersistence = ReturnType<typeof createZoomPersistence>
