import path from 'node:path'

import {
  type DesktopScreenRect,
  type DesktopSpritePosition,
  type DesktopSpriteRestPosition,
  type DesktopWindowSceneSnapshot,
  IPC,
  SPRITE_SCALE_LIMITS
} from '@ipc/contracts'
import type { BrowserWindow, IpcMain, Rectangle, Screen } from 'electron'

import { isSenderWindow } from '../security/ipc-trust'
import { atomicWriteFile, broadcastToAllWindows, errorMessage, hideAndSkipTaskbar, safeReadJson } from '../shared/utils'

const POSITION_FILE = 'companion-position.json'

export function readRestPosition(userDataDir?: string): null | DesktopSpriteRestPosition {
  if (!userDataDir) {
    return null
  }

  const parsed = safeReadJson<{ origin?: unknown; screenEdge?: unknown; x?: unknown; y?: unknown }>(
    path.join(userDataDir, POSITION_FILE)
  )

  if (
    parsed &&
    typeof parsed.x === 'number' &&
    Number.isFinite(parsed.x) &&
    typeof parsed.y === 'number' &&
    Number.isFinite(parsed.y)
  ) {
    const next: DesktopSpriteRestPosition = { x: parsed.x, y: parsed.y }
    const o = parsed.origin as { x?: unknown; y?: unknown } | null

    if (o && typeof o.x === 'number' && Number.isFinite(o.x) && typeof o.y === 'number' && Number.isFinite(o.y)) {
      next.origin = { x: o.x, y: o.y }
    }

    const edge = parsed.screenEdge as { side?: unknown; yRatio?: unknown } | null

    if (
      edge &&
      (edge.side === 'left' || edge.side === 'right') &&
      typeof edge.yRatio === 'number' &&
      Number.isFinite(edge.yRatio)
    ) {
      next.screenEdge = { side: edge.side, yRatio: Math.max(0, Math.min(1, edge.yRatio)) }
    }

    return next
  }

  return null
}

// Runner 报告原生屏幕坐标：Windows 为物理像素，须换算到 DIP；其余平台已是 DIP。
function toDipRect(screen: Screen, rect: Rectangle): Rectangle {
  return process.platform === 'win32' ? screen.screenToDipRect(null, rect) : rect
}

function isScreenRect(value: unknown): value is DesktopScreenRect {
  if (!value || typeof value !== 'object') {
    return false
  }

  const { h, w, x, y } = value as Partial<Record<keyof DesktopScreenRect, unknown>>

  return (
    typeof x === 'number' &&
    typeof y === 'number' &&
    typeof w === 'number' &&
    typeof h === 'number' &&
    [x, y, w, h].every(Number.isFinite) &&
    w > 0 &&
    h > 0
  )
}

interface SpriteIpcDeps {
  getSpriteWindow: () => BrowserWindow | null | undefined
  getUserDataDir: () => string
  log: (chunk: string) => void
  screen: Screen
  /** 窗口场景取自 Runner 的 system.get_windows；Runner 未连接时为 null。 */
  getRunnerBridge: () => null | {
    dispatch: (method: string, params: Record<string, unknown>, opts: { timeoutMs: number }) => Promise<unknown>
  }
}

export function registerSpriteIpc({ deps, ipcMain }: { deps: SpriteIpcDeps; ipcMain: IpcMain }): void {
  const { getRunnerBridge, getSpriteWindow, getUserDataDir, log, screen } = deps

  const withWindow = (fn: (win: BrowserWindow) => void) => {
    const win = getSpriteWindow()

    if (win && !win.isDestroyed()) {
      fn(win)
    }
  }

  ipcMain.on(IPC.send.spriteSetDefaultScale, (_event, payload: unknown) => {
    if (!payload || typeof payload !== 'object' || !('scale' in payload)) {
      return
    }

    const { scale } = payload

    if (
      typeof scale !== 'number' ||
      !Number.isFinite(scale) ||
      scale < SPRITE_SCALE_LIMITS.min ||
      scale > SPRITE_SCALE_LIMITS.max
    ) {
      return
    }

    broadcastToAllWindows(IPC.event.spriteDefaultScaleChanged, { scale })
  })

  ipcMain.handle(IPC.invoke.spriteHide, async () => {
    withWindow(hideAndSkipTaskbar)
  })

  ipcMain.handle(
    IPC.invoke.spriteSetIgnoreMouseEvents,
    async (_event, payload?: { forward?: boolean; ignore: boolean }) => {
      const ignore = Boolean(payload?.ignore)
      withWindow(win => win.setIgnoreMouseEvents(ignore, { forward: ignore && payload?.forward !== false }))
    }
  )

  ipcMain.handle(IPC.invoke.spriteGetPosition, async () => {
    const dir = getUserDataDir()

    if (!dir) {
      return null
    }

    return readRestPosition(dir)
  })

  ipcMain.handle(IPC.invoke.spriteGetWindowScene, async (event): Promise<DesktopWindowSceneSnapshot | null> => {
    const win = getSpriteWindow()
    const bridge = getRunnerBridge()

    if (!isSenderWindow(event.sender, win) || !win || !bridge) {
      return null
    }

    let snapshot: unknown

    try {
      snapshot = await bridge.dispatch('execute_tool', { args: {}, name: 'system.get_windows' }, { timeoutMs: 1500 })

      if (typeof snapshot === 'string') {
        snapshot = JSON.parse(snapshot) as unknown
      }
    } catch {
      return null
    }

    if (win.isDestroyed() || !snapshot || typeof snapshot !== 'object') {
      return null
    }

    const data = snapshot as { runner_instance_id?: unknown; windows?: unknown }

    if (typeof data.runner_instance_id !== 'string' || !data.runner_instance_id || !Array.isArray(data.windows)) {
      return null
    }

    const bounds = win.getContentBounds()
    const display = screen.getDisplayMatching(bounds)

    const windows = data.windows.slice(0, 128).flatMap((item, index) => {
      if (!item || typeof item !== 'object') {
        return []
      }

      const row = item as Record<string, unknown>
      const id = row.window_id
      const pid = row.pid
      const x = row.x
      const y = row.y
      const w = row.w
      const h = row.h

      if (
        typeof id !== 'string' ||
        !id ||
        typeof pid !== 'number' ||
        typeof x !== 'number' ||
        typeof y !== 'number' ||
        typeof w !== 'number' ||
        typeof h !== 'number' ||
        ![pid, x, y, w, h].every(Number.isFinite) ||
        w <= 0 ||
        h <= 0
      ) {
        return []
      }

      if (pid === process.pid) {
        return []
      }

      const rect = toDipRect(screen, { height: h, width: w, x, y })
      const windowDisplay = screen.getDisplayMatching(rect)

      return [
        {
          displayId: windowDisplay.id,
          focused: row.focused === true,
          h: rect.height,
          id,
          pid,
          visible: row.visible !== false,
          w: rect.width,
          x: rect.x,
          y: rect.y,
          zOrder: typeof row.z_order === 'number' && Number.isFinite(row.z_order) ? row.z_order : index
        }
      ]
    })

    return {
      runnerInstanceId: data.runner_instance_id,
      viewport: {
        displayId: display.id,
        height: bounds.height,
        scaleFactor: display.scaleFactor,
        width: bounds.width,
        x: bounds.x,
        y: bounds.y
      },
      windows
    }
  })

  // 仪式行走目标：原生屏幕矩形换算为精灵视口内坐标，与窗口快照同一换算。
  ipcMain.handle(IPC.invoke.spriteMapScreenRect, async (event, rect?: unknown): Promise<DesktopScreenRect | null> => {
    const win = getSpriteWindow()

    if (!isSenderWindow(event.sender, win) || !win || win.isDestroyed() || !isScreenRect(rect)) {
      return null
    }

    const dip = toDipRect(screen, { height: rect.h, width: rect.w, x: rect.x, y: rect.y })
    const bounds = win.getContentBounds()

    return { h: dip.height, w: dip.width, x: dip.x - bounds.x, y: dip.y - bounds.y }
  })

  ipcMain.handle(IPC.invoke.spriteMoveToDisplay, async (event, point?: { x: number; y: number }) => {
    const win = getSpriteWindow()

    if (
      !isSenderWindow(event.sender, win) ||
      !win ||
      !point ||
      !Number.isFinite(point.x) ||
      !Number.isFinite(point.y)
    ) {
      return
    }

    const display = screen.getDisplayNearestPoint(point)
    const currentDisplay = screen.getDisplayMatching(win.getBounds())

    if (display.id !== currentDisplay.id) {
      win.setBounds(display.workArea)
    }
  })

  ipcMain.handle(IPC.invoke.spriteSetPosition, async (_event, payload?: DesktopSpritePosition) => {
    if (!payload || !Number.isFinite(payload.x) || !Number.isFinite(payload.y)) {
      return
    }

    const win = getSpriteWindow()
    const b = win && !win.isDestroyed() ? win.getContentBounds() : null
    const origin = b ? { x: b.x, y: b.y } : undefined

    const screenEdge =
      payload.screenEdge &&
      (payload.screenEdge.side === 'left' || payload.screenEdge.side === 'right') &&
      typeof payload.screenEdge.yRatio === 'number' &&
      Number.isFinite(payload.screenEdge.yRatio)
        ? { side: payload.screenEdge.side, yRatio: Math.max(0, Math.min(1, payload.screenEdge.yRatio)) }
        : undefined

    try {
      const dir = getUserDataDir()
      await atomicWriteFile(
        path.join(dir, POSITION_FILE),
        JSON.stringify({ x: payload.x, y: payload.y, origin, ...(screenEdge ? { screenEdge } : {}) })
      )
    } catch (error) {
      log(`[sprite] saving rest position failed: ${errorMessage(error)}`)
    }
  })

  // 拖拽过程中，当光标越过视口跨到另一块显示器时，渲染层会上报超出视口的指针坐标——
  // 此处把窗口贴到光标所在的显示器上，并返回两个窗口坐标与光标点，
  // 让渲染层重映射精灵位置、并判断最新指针坐标是窗口跳转前还是跳转后采样。
  ipcMain.handle(IPC.invoke.spriteMoveToCursorDisplay, async () => {
    const win = getSpriteWindow()

    if (!win || win.isDestroyed()) {
      return null
    }

    const cursor = screen.getCursorScreenPoint()
    const target = screen.getDisplayNearestPoint(cursor)

    if (target.id === screen.getDisplayMatching(win.getBounds()).id) {
      return null
    }

    const from = win.getContentBounds()
    win.setBounds(target.workArea)

    return {
      cursor: { x: cursor.x, y: cursor.y },
      from: { x: from.x, y: from.y },
      to: { x: target.workArea.x, y: target.workArea.y }
    }
  })
}
