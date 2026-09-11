import { IPC } from '@ipc/contracts'
import type { Clipboard, IpcMain } from 'electron'

interface ClipboardIpcDeps {
  electron: { clipboard: Clipboard }
  ipcMain: IpcMain
  writeComposerImage: (buffer: Buffer, ext: string) => Promise<string>
}

export function registerClipboardIpc({ electron, ipcMain, writeComposerImage }: ClipboardIpcDeps): void {
  const { clipboard } = electron

  ipcMain.handle(IPC.invoke.writeClipboard, (_event, text) => {
    clipboard.writeText(String(text || ''))

    return true
  })

  ipcMain.handle(IPC.invoke.saveClipboardImage, async () => {
    const image = clipboard.readImage()

    if (!image || image.isEmpty()) {
      return ''
    }

    return writeComposerImage(image.toPNG(), '.png')
  })
}
