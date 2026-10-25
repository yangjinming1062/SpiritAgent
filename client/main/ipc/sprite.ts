import type { BrowserWindow, IpcMain, Screen } from 'electron'

import { type DesktopSpritePosition, IPC, SPRITE_SCALE_LIMITS } from '@ipc/contracts'
import { errorMessage } from '@runtime'

import { isSenderWindow } from '../security/ipc-trust'
import { readRestPosition, spritePositionPath } from '../shared/sprite-position'
import {
  atomicWriteFile,
  broadcastToAllWindows,
  hideAndSkipTaskbar,
  isFiniteNumber,
  setWindowIgnoreMouseEvents
} from '../shared/utils'

interface SpriteIpcDeps {
  getSpriteWindow: () => BrowserWindow | null | undefined
  stageAvailable: () => boolean
  getUserDataDir: () => string
  log: (chunk: string) => void
  screen: Screen
}

export function registerSpriteIpc({ deps, ipcMain }: { deps: SpriteIpcDeps; ipcMain: IpcMain }): void {
  const { getSpriteWindow, getUserDataDir, log, screen } = deps

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

    try {
      const dir = getUserDataDir()

      await atomicWriteFile(spritePositionPath(dir), JSON.stringify({ x: payload.x, y: payload.y, origin }))
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
