import path from 'node:path'

import type { BrowserWindow, IpcMain, IpcMainInvokeEvent, Rectangle, Screen } from 'electron'

import {
  type DesktopScreenRect,
  type DesktopSpritePosition,
  type DesktopSpriteRestPosition,
  type DesktopWindowSceneSnapshot,
  IPC,
  SPRITE_SCALE_LIMITS
} from '@ipc/contracts'
import { clamp } from '@runtime'

import { isSenderWindow } from '../security/ipc-trust'
import {
  atomicWriteFile,
  broadcastToAllWindows,
  errorMessage,
  hideAndSkipTaskbar,
  isFiniteNumber,
  safeReadJson,
  setWindowIgnoreMouseEvents
} from '../shared/utils'

const POSITION_FILE = 'companion-position.json'

// 落盘与读盘共用：贴边侧合法且纵向比例为有限数时保留，比例限制在 0 到 1。
function normalizeScreenEdge(value: unknown): DesktopSpritePosition['screenEdge'] {
  const edge = value as { side?: unknown; yRatio?: unknown } | null | undefined

  return edge && (edge.side === 'left' || edge.side === 'right') && isFiniteNumber(edge.yRatio)
    ? { side: edge.side, yRatio: clamp(edge.yRatio, 0, 1) }
    : undefined
}

export function readRestPosition(userDataDir?: string): null | DesktopSpriteRestPosition {
  if (!userDataDir) {
    return null
  }

  const parsed = safeReadJson<{ origin?: unknown; screenEdge?: unknown; x?: unknown; y?: unknown }>(
    path.join(userDataDir, POSITION_FILE)
  )

  if (!parsed || !isFiniteNumber(parsed.x) || !isFiniteNumber(parsed.y)) {
    return null
  }

  const next: DesktopSpriteRestPosition = { x: parsed.x, y: parsed.y }
  const origin = parsed.origin as { x?: unknown; y?: unknown } | null

  if (origin && isFiniteNumber(origin.x) && isFiniteNumber(origin.y)) {
    next.origin = { x: origin.x, y: origin.y }
  }

  const screenEdge = normalizeScreenEdge(parsed.screenEdge)

  if (screenEdge) {
    next.screenEdge = screenEdge
  }

  return next
}

// Runner 报告原生屏幕坐标：Windows 为物理像素，须换算到 DIP；其余平台已是 DIP。
function toDipRect(screen: Screen, { h, w, x, y }: DesktopScreenRect): Rectangle {
  const rect = { height: h, width: w, x, y }

  return process.platform === 'win32' ? screen.screenToDipRect(null, rect) : rect
}

function isScreenRect(value: unknown): value is DesktopScreenRect {
  if (!value || typeof value !== 'object') {
    return false
  }

  const { h, w, x, y } = value as Partial<Record<keyof DesktopScreenRect, unknown>>

  return isFiniteNumber(x) && isFiniteNumber(y) && isFiniteNumber(w) && isFiniteNumber(h) && w > 0 && h > 0
}

interface SpriteIpcDeps {
  getSpriteWindow: () => BrowserWindow | null | undefined
  stageAvailable: () => boolean
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

  ipcMain.on(IPC.send.spriteSetDefaultScale, (_event, payload: unknown) => {
    const scale = (payload as { scale?: unknown } | null | undefined)?.scale

    if (!isFiniteNumber(scale) || scale < SPRITE_SCALE_LIMITS.min || scale > SPRITE_SCALE_LIMITS.max) {
      return
    }

    broadcastToAllWindows(IPC.event.spriteDefaultScaleChanged, { scale })
  })

  ipcMain.handle(IPC.invoke.spriteHide, () => hideAndSkipTaskbar(getSpriteWindow()))

  ipcMain.handle(IPC.invoke.spriteSetIgnoreMouseEvents, (_event, payload?: { forward?: boolean; ignore: boolean }) =>
    setWindowIgnoreMouseEvents(getSpriteWindow(), payload)
  )

  ipcMain.handle(IPC.invoke.spriteGetPosition, () => readRestPosition(getUserDataDir()))

  // 隐藏宿主仍处理聊天，但桌面模式不授予精灵空间操作。
  const stageWindowFor = (event: IpcMainInvokeEvent): BrowserWindow | null => {
    const win = getSpriteWindow()

    if (!deps.stageAvailable() || !isSenderWindow(event.sender, win)) {
      return null
    }

    return win ?? null
  }

  ipcMain.handle(IPC.invoke.spriteGetWindowScene, async (event): Promise<DesktopWindowSceneSnapshot | null> => {
    const win = stageWindowFor(event)
    const bridge = getRunnerBridge()

    if (!win || !bridge) {
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

    if (!deps.stageAvailable() || win.isDestroyed() || !snapshot || typeof snapshot !== 'object') {
      return null
    }

    const data = snapshot as { runner_instance_id?: unknown; windows?: unknown }

    if (typeof data.runner_instance_id !== 'string' || !data.runner_instance_id || !Array.isArray(data.windows)) {
      return null
    }

    const bounds = win.getContentBounds()
    const display = screen.getDisplayMatching(bounds)

    const windows = data.windows.slice(0, 128).flatMap((item, index) => {
      if (!isScreenRect(item)) {
        return []
      }

      const row = item as DesktopScreenRect & Record<string, unknown>
      const { pid, window_id: id } = row

      if (typeof id !== 'string' || !id || !isFiniteNumber(pid) || pid === process.pid) {
        return []
      }

      const rect = toDipRect(screen, row)
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
          zOrder: isFiniteNumber(row.z_order) ? row.z_order : index
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

    if (!deps.stageAvailable()) {
      return
    }

    const display = screen.getDisplayNearestPoint(point)
    const currentDisplay = screen.getDisplayMatching(win.getBounds())

    if (display.id !== currentDisplay.id) {
      win.setBounds(display.workArea)
    }
  })

  ipcMain.handle(IPC.invoke.spriteSetPosition, async (event, payload?: DesktopSpritePosition) => {
    if (
      !deps.stageAvailable() ||
      !isSenderWindow(event.sender, getSpriteWindow()) ||
      !payload ||
      !Number.isFinite(payload.x) ||
      !Number.isFinite(payload.y)
    ) {
      return
    }

    const win = getSpriteWindow()
    const b = win && !win.isDestroyed() ? win.getContentBounds() : null
    const origin = b ? { x: b.x, y: b.y } : undefined

    const screenEdge = normalizeScreenEdge(payload.screenEdge)

    try {
      const dir = getUserDataDir()

      await atomicWriteFile(
        path.join(dir, POSITION_FILE),
        JSON.stringify({ x: payload.x, y: payload.y, origin, screenEdge })
      )
    } catch (error) {
      log(`[sprite] saving rest position failed: ${errorMessage(error)}`)
    }
  })

  // 拖拽跨屏：光标越过视口到另一显示器时窗口贴到光标所在显示器，返回两个窗口坐标与光标点，供渲染层重映射位置并判断指针采样时序。
  ipcMain.handle(IPC.invoke.spriteMoveToCursorDisplay, async event => {
    if (!deps.stageAvailable() || !isSenderWindow(event.sender, getSpriteWindow())) {
      return null
    }

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
