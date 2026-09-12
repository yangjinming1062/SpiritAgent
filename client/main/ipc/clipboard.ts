import { IPC } from '@ipc/contracts'
import type { Clipboard, IpcMain } from 'electron'

interface ClipboardIpcDeps {
  electron: { clipboard: Clipboard }
  ipcMain: IpcMain
}

export function registerClipboardIpc({ electron, ipcMain }: ClipboardIpcDeps): void {
  const { clipboard } = electron

  ipcMain.handle(IPC.invoke.writeClipboard, (_event, text) => {
    clipboard.writeText(String(text || ''))

    return true
  })
}
