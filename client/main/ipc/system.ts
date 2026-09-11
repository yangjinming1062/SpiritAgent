import { IPC } from '@ipc/contracts'
import type { App, IpcMain } from 'electron'

interface SystemIpcDeps {
  electron: { app: App }
  ipcMain: IpcMain
}

export function registerSystemIpc({ electron, ipcMain }: SystemIpcDeps): void {
  const { app } = electron

  ipcMain.handle(IPC.invoke.version, async () => ({
    appVersion: app.getVersion(),
    electronVersion: process.versions.electron,
    nodeVersion: process.versions.node,
    platform: process.platform
  }))
}
