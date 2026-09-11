import path from 'node:path'

import { IPC } from '@ipc/contracts'
import type { BrowserWindow, IpcMain, Screen } from 'electron'

import { atomicWriteFile, hideAndSkipTaskbar, safeReadJson } from '../shared/utils'

const POSITION_FILE = 'companion-position.json'

export interface RestPosition {
  x: number
  y: number
  // 保存时的窗口坐标——让下次启动能在精灵上次留下的那块显示器上重新打开精灵窗口，
  // 而不只是主显示器。
  origin?: { x: number; y: number }
}

export function readRestPosition(userDataDir?: string): null | RestPosition {
  if (!userDataDir) {
    return null
  }

  const parsed = safeReadJson<{ origin?: unknown; x?: unknown; y?: unknown }>(path.join(userDataDir, POSITION_FILE))

  if (parsed && typeof parsed.x === 'number' && typeof parsed.y === 'number') {
    const next: RestPosition = { x: parsed.x, y: parsed.y }
    const o = parsed.origin as { x?: unknown; y?: unknown } | null

    if (o && typeof o.x === 'number' && typeof o.y === 'number') {
      next.origin = { x: o.x, y: o.y }
    }

    return next
  }

  return null
}

interface SpriteIpcDeps {
  getSpriteWindow: () => BrowserWindow | null | undefined
  getUserDataDir: () => string
  screen: Screen
}

export function registerSpriteIpc({ deps, ipcMain }: { deps: SpriteIpcDeps; ipcMain: IpcMain }): void {
  const { getSpriteWindow, getUserDataDir, screen } = deps

  const withWindow = (fn: (win: BrowserWindow) => void) => {
    const win = getSpriteWindow()

    if (win && !win.isDestroyed()) {
      fn(win)
    }
  }

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

  ipcMain.handle(IPC.invoke.spriteSetPosition, async (_event, payload?: { x: number; y: number }) => {
    if (!payload || typeof payload.x !== 'number' || typeof payload.y !== 'number') {
      return
    }

    const win = getSpriteWindow()
    const b = win && !win.isDestroyed() ? win.getContentBounds() : null
    const origin = b ? { x: b.x, y: b.y } : undefined

    try {
      const dir = getUserDataDir()
      await atomicWriteFile(path.join(dir, POSITION_FILE), JSON.stringify({ x: payload.x, y: payload.y, origin }))
    } catch {
      // 尽力而为
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
