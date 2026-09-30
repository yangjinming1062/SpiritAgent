import fs from 'node:fs'
import path from 'node:path'

import { clamp } from '@runtime'
import type { App, BrowserWindow } from 'electron'

import { errorMessage, safeReadJson } from '../shared/utils'

// 缩放级别本地持久化：BrowserWindow 的 setZoomLevel 不跨重启，落盘 desktop-zoom.json 以还原。
const ZOOM_FILE = 'desktop-zoom.json'
const ZOOM_STEP = 0.1

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

  function setAndPersistZoomLevel(targetWin: BrowserWindow | null, zoomLevel: number): void {
    if (!targetWin || targetWin.isDestroyed()) {
      return
    }

    const next = clampZoomLevel(zoomLevel)
    targetWin.webContents.setZoomLevel(next)
    writePersistedZoomLevel(next)
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
    setAndPersistZoomLevel,
    /** 在当前缩放级别上放大（1）或缩小（-1）一档并持久化。 */
    stepZoomLevel(targetWin: BrowserWindow | null, direction: -1 | 1): void {
      if (targetWin && !targetWin.isDestroyed()) {
        setAndPersistZoomLevel(targetWin, targetWin.webContents.getZoomLevel() + direction * ZOOM_STEP)
      }
    }
  }
}

export type ZoomPersistence = ReturnType<typeof createZoomPersistence>
