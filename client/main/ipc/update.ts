import {
  type DesktopUpdateEvent,
  type DesktopUpdateInfo,
  type DesktopUpdateProgress,
  IPC,
  type IpcEventContract
} from '@ipc/contracts'
import type { App, BrowserWindow, IpcMain } from 'electron'
import log from 'electron-log/main'
// 顶层静态 import：client/package.json 是 ESM (`"type": "module"`)，asar 模式下 dynamic require
// （`require('electron-log/main')` / `require('electron-updater')`）会被 Node 拒绝并抛
// "Dynamic require of 'electron-log/main' is not supported"。把这两条搬上来既消除错误，
// 也让 esbuild 在打包期把 CJS 入口转成 ESM-friendly 的 default import。
// 注意：electron-updater 是 CJS 模块没有 named export `autoUpdater`，必须 default import + 解构，
// 顶层 named import 在 dev/prod 都会被 Node ESM loader 拒绝。
import electronUpdaterPkg from 'electron-updater'

import { errorMessage } from '../shared/utils'

interface UpdateIpcDeps {
  electron: { app: App }
  getMainWindow: () => BrowserWindow | null | undefined
  ipcMain: IpcMain
  isFeedConfigured?: () => boolean
  sendToMain: <C extends keyof IpcEventContract>(
    win: BrowserWindow | null | undefined,
    channel: C,
    ...payload: IpcEventContract[C]
  ) => void
}

/** electron-updater 的 releaseNotes 可能是 string | ReleaseNoteInfo[] | null | undefined；
 *  IPC 契约的 DesktopUpdateInfo 只承诺 string | undefined——把 null/note[] 归一化成 undefined。
 *  之前 dynamic require 把类型擦成 any，TS 没看出这里不兼容；顶层静态 import 让 TS 抓出来。 */
function toDesktopUpdateInfo(info: unknown): DesktopUpdateInfo {
  const candidate = (info ?? {}) as DesktopUpdateInfo & { releaseNotes?: unknown }
  const notes = candidate.releaseNotes

  return {
    ...candidate,
    releaseNotes: typeof notes === 'string' ? notes : undefined
  }
}

export function registerUpdateIpc({
  electron,
  getMainWindow,
  ipcMain,
  isFeedConfigured,
  sendToMain
}: UpdateIpcDeps): void {
  const { app } = electron

  function broadcast(event: DesktopUpdateEvent): void {
    const win = getMainWindow()
    sendToMain(win, IPC.event.updateEvent, event)
  }

  // 始终注册 updateCheck：开发模式无更新源，回 'none' 让渲染层落 "up to date" 文案，
  // 避免 renderer 触发未注册 IPC handler 抛出 unhandled rejection。
  ipcMain.handle(IPC.invoke.updateCheck, async () => {
    if (!app.isPackaged || (isFeedConfigured && !isFeedConfigured())) {
      broadcast({ type: 'none' })

      return
    }

    try {
      await autoUpdater.checkForUpdates()
    } catch (e: unknown) {
      const msg = errorMessage(e)
      broadcast({ message: msg, type: 'error' })
    }
  })

  if (!app.isPackaged) {
    return
  }

  const { autoUpdater } = electronUpdaterPkg
  autoUpdater.logger = log

  autoUpdater.on('checking-for-update', () => broadcast({ type: 'checking' }))
  autoUpdater.on('update-available', info => broadcast({ info: toDesktopUpdateInfo(info), type: 'available' }))
  autoUpdater.on('update-not-available', info => broadcast({ info: toDesktopUpdateInfo(info), type: 'none' }))
  autoUpdater.on('download-progress', (progress: DesktopUpdateProgress) => broadcast({ progress, type: 'progress' }))
  autoUpdater.on('update-downloaded', info => broadcast({ info: toDesktopUpdateInfo(info), type: 'downloaded' }))
  autoUpdater.on('error', (err: unknown) => {
    const message = errorMessage(err)

    if (message.includes('404') && message.includes('latest.yml')) {
      broadcast({ info: undefined, type: 'none' })
    } else {
      broadcast({ message, type: 'error' })
    }
  })
}
